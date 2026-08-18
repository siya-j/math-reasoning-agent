"""End-to-end integration: goal in, ProofRun out. No API key, no Lean, no SIF.

The agent is replaced by a scripted one that CALLS THE REAL TOOLS, so the log,
the budget, the guard and the conversion are all produced by the code path a
live run would use. Only the model and the compiler are fakes.

`test_a_second_goal_cannot_inherit_the_first_goals_proof` is the one that
matters most. The budget and the proof record live in the workspace, so a
shared directory would let goal 2 be reported as proved on goal 1's evidence.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import config
from domain.verdict import VerificationStatus
from math_v2 import _local, harness
from math_v2.core import budget, log
from math_v2.tools import _util

ROOT = Path(__file__).resolve().parent.parent


def scripted(script):
    """An agent that performs `script` — a list of (tool_name, kwargs).

    It calls the REAL tools through their real interfaces, so everything the
    conversion reads was produced the way a live run produces it.
    """

    def factory(model, tools, system_prompt):
        by_name = {t.name: t for t in tools}
        factory.prompt = system_prompt
        factory.tools = list(by_name)

        class Agent:
            # ASYNC, like the real graph. The tools are `async def`, so a
            # synchronous fake that nests `asyncio.run` models an interface
            # the agent does not have — and hid the sync-invocation bug.
            async def ainvoke(self, payload, context=None):
                from langchain.tools import ToolRuntime

                runtime = ToolRuntime(
                    state=None, context=context, config={},
                    stream_writer=lambda *a, **k: None,
                    tool_call_id="t", store=None,
                )
                for name, kwargs in script:
                    await by_name[name].ainvoke({**kwargs, "runtime": runtime})
                return {"messages": [type("M", (), {"text": "finished"})()]}

        return Agent()

    return factory


@pytest.fixture
def compiler_accepts(monkeypatch):
    from verifiers.lean_runner import LeanOutcome, LeanResult

    async def ok(source):
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: ok)


@pytest.fixture
def compiler_rejects(monkeypatch):
    from verifiers.lean_runner import LeanOutcome, LeanResult

    async def no(source):
        return LeanResult(LeanOutcome.ERRORS, "f.lean:4:2: error: unsolved goals\n⊢ P")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: no)


STATEMENT = "theorem mra_goal : 2 + 2 = 4"


# ---------------------------------------------------------------- conversion
def test_a_proved_goal_becomes_a_proved_ProofRun(tmp_path, compiler_accepts):
    run = harness.prove(
        "does 2 + 2 = 4?",
        model=object(),
        workdir=str(tmp_path),
        agent_factory=scripted([
            ("check_statement", {"statement": STATEMENT}),
            ("try_proof", {"proof": "by norm_num"}),
            ("finish", {"summary": "done", "outcome": "proved",
                        "statement": STATEMENT, "claim": "does 2 + 2 = 4?"}),
        ]),
    )

    assert run.proved
    assert run.statement == STATEMENT
    assert run.proof == "by norm_num"
    assert run.verdict.status is VerificationStatus.TRUE


def test_a_failed_goal_becomes_not_proved_and_never_false(tmp_path, compiler_rejects):
    run = harness.prove(
        "is P true?",
        model=object(),
        workdir=str(tmp_path),
        agent_factory=scripted([
            ("check_statement", {"statement": STATEMENT}),
            ("try_proof", {"proof": "nonsense"}),
        ]),
    )

    assert not run.proved
    assert run.verdict.status is VerificationStatus.UNKNOWN, (
        "a failed proof was reported as a refutation"
    )
    assert "not evidence that the claim is false" in run.verdict.detail


def test_the_verdict_is_re_derived_and_not_taken_from_finish(tmp_path,
                                                             compiler_rejects):
    """A harness bug must not be able to promote what the guard refused."""
    run = harness.prove(
        "is P true?",
        model=object(),
        workdir=str(tmp_path),
        agent_factory=scripted([
            ("check_statement", {"statement": STATEMENT}),
            ("try_proof", {"proof": "nonsense"}),
            # The agent claims a proof it does not have.
            ("finish", {"summary": "proved it!", "outcome": "proved",
                        "statement": STATEMENT}),
        ]),
    )
    assert not run.proved


def test_a_lemma_is_not_converted_into_a_proof_of_the_goal(tmp_path,
                                                           compiler_accepts):
    run = harness.prove(
        "is P true?",
        model=object(),
        workdir=str(tmp_path),
        agent_factory=scripted([
            ("check_statement", {"statement": STATEMENT}),
            ("try_lemma", {"statement": "lemma helper : True", "proof": "trivial"}),
        ]),
    )

    assert not run.proved, "a helper lemma was read as the goal"
    assert any(a.verdict.status is not VerificationStatus.TRUE for a in run.attempts)


def test_attempts_and_trace_survive_the_conversion(tmp_path, compiler_rejects):
    run = harness.prove(
        "is P true?", model=object(), workdir=str(tmp_path),
        agent_factory=scripted([
            ("check_statement", {"statement": STATEMENT}),
            ("try_proof", {"proof": "one"}),
            ("try_proof", {"proof": "two"}),
        ]),
    )

    # Two PROOF attempts. The statement check is a check, not an attempt.
    assert len(run.attempts) == 2
    assert any("statement check" in entry for entry in run.trace)
    assert any("execution mode" in entry for entry in run.trace)
    assert run.telemetry.lean_calls == 3   # the check plus the two attempts


def test_an_agent_crash_still_returns_what_was_recorded(tmp_path, compiler_accepts):
    def exploding(model, tools, system_prompt):
        class Agent:
            async def ainvoke(self, payload, context=None):
                raise RuntimeError("model provider is down")

        return Agent()

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=exploding)
    assert not run.proved
    assert run.verdict is not None, "a crash produced no verdict at all"


# ------------------------------------------------------- fresh workdir/goal
def test_a_second_goal_cannot_inherit_the_first_goals_proof(tmp_path,
                                                            compiler_accepts):
    """THE isolation test. Shared state would report goal 2 proved on goal 1."""
    shared = str(tmp_path)

    harness.prove("first", model=object(), workdir=shared,
                  agent_factory=scripted([
                      ("check_statement", {"statement": STATEMENT}),
                      ("try_proof", {"proof": "by norm_num"}),
                  ]))

    # Same directory, deliberately: prove() must clear it regardless.
    second = harness.prove("second", model=object(), workdir=shared,
                           agent_factory=scripted([]))

    assert not second.proved, "goal 2 inherited goal 1's accepted proof"
    assert second.attempts == []


def test_each_goal_gets_its_own_directory_when_none_is_given(compiler_accepts):
    seen = []

    def capture(model, tools, system_prompt):
        class Agent:
            async def ainvoke(self, payload, context=None):
                seen.append(context.workdir)
                return {"messages": []}

        return Agent()

    harness.prove("a", model=object(), agent_factory=capture)
    harness.prove("b", model=object(), agent_factory=capture)

    assert len(set(seen)) == 2, "two goals shared a workspace"


def test_the_budget_is_reset_for_each_goal(tmp_path, compiler_accepts):
    shared = str(tmp_path)
    harness.prove("first", model=object(), workdir=shared,
                  agent_factory=scripted([
                      ("check_statement", {"statement": STATEMENT}),
                      ("try_proof", {"proof": "a"}),
                  ]))
    assert budget.read(shared)["lean_calls"] == 2

    harness.prove("second", model=object(), workdir=shared,
                  agent_factory=scripted([]))
    assert budget.read(shared)["lean_calls"] == 0


# ------------------------------------------------------------------ dispatch
def test_the_prover_selector_routes_math_v2(monkeypatch, tmp_path):
    """`evaluate_proofs.py` reads config.PROVER and nothing else."""
    from pipeline import proving

    monkeypatch.setattr(config, "PROVER", proving.MATH_V2)
    called = {}

    def fake_prove(goal, **kwargs):
        called["goal"] = goal
        from domain.proof import ProofRun

        return ProofRun(goal=goal)

    monkeypatch.setattr(harness, "prove", fake_prove)
    run = proving.prove("a claim", depth=1, reviewer=None)

    assert called["goal"] == "a claim"
    assert run.goal == "a claim"


def test_the_other_provers_still_route(monkeypatch):
    from pipeline import proving

    assert (proving.PIPELINE, proving.AGENTIC, proving.MATH_V2) == (
        "pipeline", "agentic", "math_v2"
    )


def test_evaluate_proofs_never_imports_a_prover_directly():
    """Constraint: the evaluator drives whichever prover is CONFIGURED.

    Naming one of them turns a switch into a dependency. When the Lean backend
    had to be recorded in the results file, the description was routed through
    `pipeline.proving.environment()` — the same seam as `prove` — rather than
    importing `math_v2.tools._repl` here.
    """
    source = (ROOT / "scripts" / "evaluate_proofs.py").read_text("utf-8")
    assert "math_v2" not in source
    assert "agentic_prover" not in source
    assert "from pipeline.proving import" in source
    assert "prove" in source and "environment" in source


# ------------------------------------------------------------ local backend
def test_local_mode_is_off_unless_asked_for():
    """The Aura/SIF path stays the default; local is development-only."""
    assert _local.MODE in ("", "local")
    if not os.getenv("MRA_EXEC"):
        assert not _local.enabled()
        assert _util.mode() == "dispatch"


def test_both_execution_modes_build_the_same_argv():
    """If they diverged, a local run would stop predicting a dispatched one."""
    assert _util.lean_argv("/w/c.lean") == ["lake", "env", "lean", "/w/c.lean"]

    # Everything after the interpreter must match. The interpreter itself
    # cannot: `python3` exists in the SIF by construction and may not exist on
    # a host at all — Windows usually has no `python3`, which is what broke the
    # local worker there while every mocked test passed.
    assert _util.worker_argv("check_primality")[1:] == [
        "-m", "math_worker", "check_primality"
    ]
    assert _util.worker_argv("check_primality", python="python3")[0] == "python3"


def test_the_local_worker_uses_an_interpreter_that_exists(monkeypatch):
    import sys

    monkeypatch.setattr(_local, "MODE", "local")
    assert _util.worker_argv("check_primality")[0] == sys.executable

    monkeypatch.setattr(_local, "MODE", "")
    assert _util.worker_argv("check_primality")[0] == "python3"


def test_the_local_runner_returns_a_result_rather_than_raising(tmp_path):
    result = asyncio.run(_local.run(["definitely-not-a-command"], str(tmp_path)))
    assert result.ok is False
    assert result.returncode == -1


def test_the_local_runner_honours_a_timeout(tmp_path):
    # A real directory from the fixture, not a hardcoded "/tmp": that is not a
    # path on Windows and the subprocess cwd fails with WinError 267 before the
    # timeout under test is ever reached.
    result = asyncio.run(
        _local.run([sys.executable, "-c", "import time; time.sleep(5)"],
                   str(tmp_path), timeout=0.4)
    )
    assert result.ok is False
    assert "timed out" in result.stderr


def test_the_worker_really_runs_under_the_local_backend(tmp_path, monkeypatch):
    """The one place the local path is exercised end to end, for real."""
    monkeypatch.setattr(_local, "MODE", "local")
    dispatch = _util.worker_dispatch(str(tmp_path))

    envelope = asyncio.run(dispatch("check_primality", {"lhs": "561"}))

    assert envelope["ok"] is True
    assert envelope["outputs"]["status"] == "false"      # 561 = 3 x 11 x 17


def test_local_mode_reports_itself_so_a_number_is_never_unattributed(monkeypatch):
    monkeypatch.setattr(_local, "MODE", "local")
    assert _util.mode() == "local"
    assert _util.stdin_unsupported() is False


def test_lean_availability_is_reported_rather_than_assumed(monkeypatch):
    """A run that silently scored 0% for a missing compiler would be worse."""
    monkeypatch.setattr(_local, "LEAN_PROJECT", "")
    ok, why = _local.lean_available()
    assert ok is False and "MRA_LEAN_PROJECT" in why


# ------------------------------------------------------------ benchmark preset
def test_the_benchmark_preset_matches_the_previous_experiment():
    """900s/12 compared against 300s/8 would be a different experiment."""
    assert budget.BENCHMARK_2026_08 == {
        "MRA_MAX_AGENT_SECONDS": "300",
        "MRA_MAX_AGENT_LEAN": "8",
        "MRA_MAX_AGENT_STEPS": "20",
        "MRA_MAX_AGENT_SEARCHES": "8",
        "MRA_MAX_CONSECUTIVE_SEARCHES": "3",
    }


def test_the_larger_defaults_are_still_there():
    """The preset is applied through the environment, not by lowering these."""
    assert (budget.MAX_SECONDS, budget.MAX_LEAN_CALLS) == (900.0, 12) or os.getenv(
        "MRA_MAX_AGENT_SECONDS"
    )


def test_every_preset_key_is_a_variable_the_budget_actually_reads():
    """A preset key with no reader would silently do nothing."""
    source = (ROOT / "math_v2" / "core" / "budget.py").read_text("utf-8")
    for name in budget.BENCHMARK_2026_08:
        assert f'os.getenv("{name}"' in source, name


# ------------------------------------------------------------------ goals
def test_the_thirteen_evaluation_goals_are_reusable_unchanged():
    from eval.proof_dataset import Tier, load_goals

    goals = load_goals()
    near = [g for g in goals if g.tier is Tier.NEAR_MATHLIB]
    inside = [g for g in goals if g.tier is Tier.IN_MATHLIB]

    assert (len(near), len(inside)) == (7, 6)
    # The agent takes the English claim directly; nothing else is required.
    assert all(g.goal.strip() for g in near + inside)
