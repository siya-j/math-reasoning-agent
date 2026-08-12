"""The Aura seam, and `finish`.

Two interfaces could not be verified against the real repo, so the tests below
pin down what we do about that rather than what we assume.

`test_finish_refuses_a_proof_no_compilation_supports` is the one that matters.
It is the guard, at the only point where the system speaks to a human.
"""

import asyncio
import dataclasses
import json

import pytest

from math_v2 import _aura
from math_v2.context import MathContext
from math_v2.core import log, verdict
from math_v2.tools import _util
from math_v2.tools.control import finish
from verifiers.lean_runner import LeanOutcome

STATEMENT = "theorem mra_goal : 2 + 2 = 4"


def run(coro):
    return asyncio.run(coro)


# The two CommandSpec shapes we were told about. The blueprint documents the
# second; the file on disk defines the first.
@dataclasses.dataclass
class OldSpec:
    runtime: str
    workdir: str
    argv: list
    env: dict = None
    stdin: str = None
    metadata: dict = None
    user_id: str = None


@dataclasses.dataclass
class NewSpec:
    runtime: str
    workdir: str
    argv: list
    env: dict = None
    stdin: str = None
    metadata: dict = None
    sandbox_policy: str = "compute"
    timeout: float = 1800.0
    resources: object = None


@dataclasses.dataclass
class Resources:
    cpus: int = 1
    memory_gb: int = 1
    gpus: int = 0


@dataclasses.dataclass
class Result:
    ok: bool = True
    returncode: int = 0
    stdout: str = ""
    stdout_path: str = ""
    stderr_path: str = ""


@pytest.fixture(autouse=True)
def reset_dropped():
    _aura._dropped.clear()
    yield
    _aura._dropped.clear()


def use(spec_cls, resources=None, result=None, monkeypatch=None):
    monkeypatch.setattr(_aura, "CommandSpec", spec_cls)
    monkeypatch.setattr(_aura, "Resources", resources)

    async def fake_run(spec):
        fake_run.spec = spec
        return result or Result()

    monkeypatch.setattr(_aura, "run", fake_run)
    return fake_run


# ------------------------------------------------- surviving the contradiction
def test_a_spec_missing_timeout_and_resources_still_dispatches(monkeypatch):
    """The on-disk CommandSpec has neither. Sending them would fail every call."""
    use(OldSpec, None, monkeypatch=monkeypatch)

    spec = _aura.command_spec(argv=["lake"], workdir="/w", tool="lean")

    assert spec.runtime == "math"
    assert set(_aura.dropped_fields()) == {"timeout", "sandbox_policy", "resources"}


def test_a_spec_that_accepts_them_receives_them(monkeypatch):
    """And on the version the blueprint documents, we ask for what we need."""
    use(NewSpec, Resources, monkeypatch=monkeypatch)

    spec = _aura.command_spec(argv=["lake"], workdir="/w", tool="lean")

    assert spec.timeout == _aura.DEFAULT_TIMEOUT
    assert spec.resources.memory_gb == _aura.DEFAULT_MEMORY_GB
    assert _aura.dropped_fields() == []


def test_the_memory_request_stays_on_the_ephemeral_pool():
    """Above 8 GB every proof routes to SLURM (§7.2). Mathlib needs 4-8."""
    assert _aura.DEFAULT_MEMORY_GB <= 8


def test_dropped_fields_are_reported_and_not_swallowed(monkeypatch):
    use(OldSpec, None, monkeypatch=monkeypatch)
    _aura.command_spec(argv=["x"], workdir="/w", tool="t")
    assert "timeout" in _aura.dropped_fields()


# ------------------------------------------------------------ lean dispatch
def test_the_source_is_written_into_the_workspace_and_compiled(tmp_path, monkeypatch):
    fake = use(NewSpec, Resources, result=Result(ok=True, stdout=""),
               monkeypatch=monkeypatch)

    result = run(_util.lean_runner(str(tmp_path))("import Mathlib\ntheorem t : True := trivial"))

    assert result.outcome is LeanOutcome.COMPILED
    assert fake.spec.argv[:3] == ["lake", "env", "lean"]
    written = fake.spec.argv[3]
    assert str(tmp_path) in written, "Lean was pointed outside the only writable mount"
    assert open(written, encoding="utf-8").read().startswith("import Mathlib")


def test_the_anticheat_runs_on_the_way_back_not_in_the_prompt(tmp_path, monkeypatch):
    """`sorry` compiles and proves nothing. The model cannot opt out of this."""
    use(NewSpec, Resources, result=Result(ok=True, stdout=""), monkeypatch=monkeypatch)

    result = run(_util.lean_runner(str(tmp_path))("theorem t : True := by sorry"))
    assert result.outcome is LeanOutcome.INCOMPLETE

    result = run(_util.lean_runner(str(tmp_path))("axiom cheat : False\ntheorem t : True := trivial"))
    assert result.outcome is LeanOutcome.CHEATED


def test_a_backend_failure_becomes_a_result_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(_aura, "CommandSpec", NewSpec)
    monkeypatch.setattr(_aura, "Resources", Resources)

    async def explode(spec):
        raise RuntimeError("queue is down")

    monkeypatch.setattr(_aura, "run", explode)

    result = run(_util.lean_runner(str(tmp_path))("theorem t : True := trivial"))
    assert result.outcome is LeanOutcome.UNAVAILABLE, "a broken queue crashed the graph"


# ---------------------------------------------------------- worker dispatch
def test_the_worker_receives_its_arguments_on_stdin(tmp_path, monkeypatch):
    payload = json.dumps({"ok": True, "outputs": {"status": "true", "detail": ""}})
    fake = use(NewSpec, Resources, result=Result(ok=True, stdout=payload),
               monkeypatch=monkeypatch)

    envelope = run(_util.worker_dispatch(str(tmp_path))("check_primality", {"lhs": "7"}))

    assert envelope["outputs"]["status"] == "true"
    assert fake.spec.argv == ["python3", "-m", "math_worker", "check_primality"]
    assert json.loads(fake.spec.stdin) == {"lhs": "7"}


def test_a_spec_without_stdin_is_detectable(monkeypatch):
    """The worker reads arguments from stdin; without it every op gets {}."""
    @dataclasses.dataclass
    class NoStdin:
        runtime: str
        workdir: str
        argv: list

    monkeypatch.setattr(_aura, "CommandSpec", NoStdin)
    assert _util.stdin_unsupported()

    monkeypatch.setattr(_aura, "CommandSpec", NewSpec)
    assert not _util.stdin_unsupported()


def test_non_json_from_the_worker_is_reported_not_parsed(tmp_path, monkeypatch):
    use(NewSpec, Resources, result=Result(ok=True, stdout="Traceback ..."),
        monkeypatch=monkeypatch)

    envelope = run(_util.worker_dispatch(str(tmp_path))("check_primality", {"lhs": "7"}))
    assert envelope["ok"] is False


# ------------------------------------------------------------------- finish
def runtime_for(workdir):
    """A REAL ToolRuntime. A stand-in is rejected by the tool's own validation.

    Worth knowing: `@tool` validates the injected runtime's type, so a duck-typed
    fake never reaches the function body.
    """
    from langchain.tools import ToolRuntime

    return ToolRuntime(
        state=None,
        context=MathContext(workdir=str(workdir)),
        config={},
        stream_writer=lambda *a, **k: None,
        tool_call_id="test",
        store=None,
    )


def call_finish(workdir, **kwargs):
    kwargs.setdefault("summary", "done")
    return run(finish.ainvoke({**kwargs, "runtime": runtime_for(workdir)}))


def test_finish_refuses_a_proof_no_compilation_supports(tmp_path):
    """THE guard. Prose does not establish a proof, at the point it is spoken."""
    result = call_finish(tmp_path, outcome="proved", statement=STATEMENT)

    assert result["accepted"] is False
    assert "REFUSED" in result["message"]


def test_finish_accepts_a_proof_the_record_supports(tmp_path):
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=STATEMENT,
                                         proof="by norm_num", status=log.TRUE))

    result = call_finish(tmp_path, outcome="proved", statement=STATEMENT)
    assert result["accepted"] is True
    assert result["outcome"] == "proved"


def test_a_proof_claim_must_name_the_statement(tmp_path):
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=STATEMENT,
                                         proof="by norm_num", status=log.TRUE))

    result = call_finish(tmp_path, outcome="proved")
    assert result["accepted"] is False
    assert result["error"] == "statement_required"


def test_a_proof_of_another_claim_cannot_be_reused(tmp_path):
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement="theorem other : True",
                                         proof="trivial", status=log.TRUE))

    result = call_finish(tmp_path, outcome="proved", statement=STATEMENT)
    assert result["accepted"] is False


def test_a_computation_cannot_be_reported_as_a_proof(tmp_path):
    result = call_finish(tmp_path, outcome="verified_true", statement=STATEMENT)
    assert result["accepted"] is False
    assert "cannot establish a proof" in result["message"]


def test_reporting_a_failure_honestly_is_always_allowed(tmp_path):
    result = call_finish(tmp_path, outcome="not_proved")
    assert result["accepted"] is True


def test_finish_reports_what_the_installation_could_not_ask_for(tmp_path, monkeypatch):
    use(OldSpec, None, monkeypatch=monkeypatch)
    _aura.command_spec(argv=["x"], workdir="/w", tool="t")

    result = call_finish(tmp_path, outcome="not_proved")
    assert any("timeout" in w for w in result["warnings"])


def test_finish_counts_the_record_rather_than_asking(tmp_path):
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=STATEMENT,
                                         status=log.UNKNOWN))
    log.keep_lemma(str(tmp_path), "lemma h : True := trivial")

    result = call_finish(tmp_path, outcome="not_proved")
    assert result["record"] == {"attempts": 1, "lemmas_kept": 1, "computations": 0}


def test_the_outcome_vocabulary_is_closed():
    """A seventh outcome would be a silent no-op in the guard."""
    import typing

    from math_v2.tools._enums import OutcomeLit

    assert set(typing.get_args(OutcomeLit)) == {
        verdict.PROVED, verdict.NOT_PROVED, verdict.NOT_FORMALIZED,
        verdict.VERIFIED_TRUE, verdict.VERIFIED_FALSE, verdict.NOT_VERIFIED,
    }
