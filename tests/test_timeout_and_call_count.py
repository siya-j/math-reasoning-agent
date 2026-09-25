"""A timed-out compile is a timeout, not a rejection; SymPy's messages bracket
the right side; and every real model call is charged exactly once.

All three were found by running the agent inside Aura (see the Aura branch
feature/math-agent-integration, vendored at 0995f62):

* A statement check killed at 180s came back as `LeanOutcome.ERRORS`, the model
  was told "Lean rejected the proof" / "the fault is in the signature", and it
  rewrote a correct `x : ℝ` as `x : Real`.
* `check_equality` reported `simplify((x+1)**2 - x**2 + 2*x + 1) = 0`, which
  reads as a different (false) identity.
* The math agent made 17 model calls and its budget recorded 16: the limiter
  charged in `after_model`, which another middleware's jump skips.
"""

import asyncio

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from math_v2 import _aura, _local, call_limit
from math_v2.context import MathContext
from math_v2.core import budget, proving
from math_v2.tools import _util
from verifiers.lean_runner import LeanOutcome, LeanResult
from verifiers.lean_verifier import interpret
from verifiers.sympy_verifier import SymPyVerifier

STATEMENT = "theorem mra_goal (x : ℝ) : (x + 1) ^ 2 = x ^ 2 + 2 * x + 1"


def run(coro):
    return asyncio.run(coro)


def timed_out(source):
    async def runner(_source):
        return LeanResult(LeanOutcome.TIMEOUT, "timed out after 180s, not rejected")
    return runner


# ------------------------------------------------ recognising a killed compile


def test_a_local_timeout_is_recognised():
    assert _aura.timed_out(_local.Result(False, -1, "", "timed out after 180s"))


def test_an_aura_timeout_is_recognised_from_the_stderr_log(tmp_path):
    log = tmp_path / "stderr.txt"
    log.write_text("command exceeded timeout of 180.0s and was killed\n", encoding="utf-8")

    class ExecutionResult:
        ok, returncode, stdout_path, stderr_path = False, -1, "", str(log)

    assert _aura.timed_out(ExecutionResult())


def test_an_ordinary_failure_is_not_a_timeout(tmp_path):
    log = tmp_path / "stderr.txt"
    log.write_text("claim.lean:3:2: error: unsolved goals\n", encoding="utf-8")

    class ExecutionResult:
        ok, returncode, stdout_path, stderr_path = False, 1, "", str(log)

    assert not _aura.timed_out(ExecutionResult())
    assert not _aura.timed_out(_local.Result(True, 0, "", "timed out after 180s"))


def test_a_timed_out_compile_is_classified_as_timeout(monkeypatch, tmp_path):
    async def killed(argv, workdir, stdin=None, timeout=180.0, cwd=None):
        return _local.Result(False, -1, "", f"timed out after {timeout:.0f}s")

    monkeypatch.setattr(_local, "MODE", "local")
    monkeypatch.setattr(_local, "run", killed)

    ok, text, _ = run(_util._subprocess_compile("import Mathlib\n" + STATEMENT + " := by ring\n", str(tmp_path)))
    result = _util._classify("x", text, ok)

    assert result.outcome is LeanOutcome.TIMEOUT
    assert "not rejected" in result.output and "180s" in result.output


def test_a_rejection_is_still_errors():
    assert _util._classify("x", "claim.lean:3:2: error: unsolved goals", False).outcome is LeanOutcome.ERRORS


def test_a_local_failure_keeps_its_stderr_in_the_detail():
    detail = _aura.failure_detail(_local.Result(False, 1, "", "lake: unknown package 'Mathlib'"))
    assert "unknown package" in detail


# ------------------------------------------------------ what the model is told


def test_the_timeout_verdict_never_reads_as_a_rejection():
    detail = interpret(LeanResult(LeanOutcome.TIMEOUT, "timed out after 180s, not rejected"), STATEMENT).detail
    assert "timed out after 180s, not rejected" in detail
    assert "rejected the proof" not in detail


def test_a_timed_out_statement_check_is_flagged_and_not_blamed_on_the_signature(tmp_path):
    reply = run(proving.check_statement(str(tmp_path), STATEMENT, timed_out(STATEMENT)))

    assert reply["outputs"]["timed_out"] is True
    assert reply["outputs"]["infra_failure"] is True
    assert reply["message"].startswith("TIMED OUT, NOT REJECTED.")
    assert "timed out after 180s" in reply["message"]
    assert "fault is in the signature" not in reply["message"]


def test_a_timed_out_proof_is_flagged_and_not_reported_as_rejected(tmp_path):
    budget.reset(str(tmp_path))
    reply = run(proving.try_proof(str(tmp_path), STATEMENT, "by rw [add_sq]; ring", timed_out(STATEMENT)))

    assert reply["outputs"]["timed_out"] is True
    assert reply["outputs"]["accepted"] is False
    assert reply["message"].startswith("TIMED OUT, NOT REJECTED.")  # try_proof's rejections start "REJECTED."


# ---------------------------------------------------------- SymPy's messages


def request(kind, lhs, rhs, **extra):
    return VerificationRequest(kind=kind, lhs=lhs, rhs=rhs, **extra)


def test_the_equality_message_brackets_a_multi_term_right_side():
    verdict = SymPyVerifier().verify(request(VerificationKind.EQUALITY, "(x+1)**2", "x**2 + 2*x + 1", variable="x"))
    assert verdict.status is VerificationStatus.TRUE
    assert "simplify((x+1)**2 - (x**2 + 2*x + 1)) = 0" in verdict.detail


def test_a_single_term_right_side_is_left_bare():
    verdict = SymPyVerifier().verify(request(VerificationKind.EQUALITY, "x + x", "2*x", variable="x"))
    assert "simplify(x + x - (2*x)) = 0" in verdict.detail
    verdict = SymPyVerifier().verify(request(VerificationKind.EQUALITY, "2*x - x", "x", variable="x"))
    assert "simplify(2*x - x - x) = 0" in verdict.detail


# --------------------------------------------------------- model-call count


class LoopBackOnce(AgentMiddleware):
    """Stands in for Aura's SpokeSelfValidationMiddleware: at the first natural
    stop it sends the model round once more, by jumping from `after_model`."""

    def __init__(self):
        super().__init__()
        self.looped = False

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        if self.looped:
            return None
        self.looped = True
        return {"messages": [HumanMessage(content="Check your answer.")], "jump_to": "model"}


@pytest.mark.parametrize("limiter_first", [True, False], ids=["limiter-outer", "limiter-inner"])
def test_every_real_model_call_is_charged_even_when_another_hook_jumps(tmp_path, limiter_first):
    class Fake(GenericFakeChatModel):
        calls: int = 0

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            self.calls += 1
            return super()._generate(*args, **kwargs)

    model = Fake(messages=iter([AIMessage(content="first answer"), AIMessage(content="checked answer")]))
    cap = call_limit.limiter(40)
    middleware = [cap, LoopBackOnce()] if limiter_first else [LoopBackOnce(), cap]
    agent = create_agent(model=model, tools=[], middleware=middleware, context_schema=MathContext)
    workdir = str(tmp_path)
    budget.reset(workdir)

    agent.invoke({"messages": [{"role": "user", "content": "q"}]}, context=MathContext(workdir=workdir))

    assert model.calls == 2
    assert budget.model_calls(workdir) == model.calls
