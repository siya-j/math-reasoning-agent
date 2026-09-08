"""How often each guard refused must be recorded, or attribution is a story.

MEASURED as a hole, on eval/results/failures-after-decompose.json. Three goals
converted to `proved` in a run carrying the decomposition redirect and the
drift refusal, and NOTHING in the results file could say whether either guard
had fired -- because the guards deliberately write no log entry ("a refusal is
not an attempt", and recording them there would inflate every attempt-based
metric). `scripts/compare_runs.py` printed "fired: NOTHING" for every goal in
a run where the redirect had demonstrably worked.

So this is a COUNTER, not a log record: the proof log stays exactly as clean
as the guards intend, and the question "did it fire" gets an answer anyway.
"""

import asyncio

import pytest
from langchain.tools import ToolRuntime

from math_v2.context import MathContext
from math_v2.core import budget, log
from math_v2.tools import proving as proving_tools
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = "theorem mra_goal (n : Nat) : n + 0 = n"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def rt(tmp_path, monkeypatch):
    async def no(source):
        return LeanResult(LeanOutcome.ERRORS, "3:2: error: unsolved goals")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda w: no)
    workdir = str(tmp_path)
    log.clear(workdir)
    budget.reset(workdir)
    log.set_goal(workdir, GOAL)
    return ToolRuntime(state=None, context=MathContext(workdir=workdir),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


def _counts(rt):
    return budget.read(rt.context.workdir).get("refusals") or {}


# ------------------------------------------------------------ the counter
def test_a_refusal_is_counted(rt):
    """`placeholder_proof`, the cheapest refusal to trigger."""
    run(proving_tools.try_proof.ainvoke(
        {"proof": "by sorry", "statement": GOAL, "runtime": rt}))

    assert _counts(rt) == {"placeholder_proof": 1}


def test_repeats_accumulate(rt):
    for _ in range(3):
        run(proving_tools.try_proof.ainvoke(
            {"proof": "by sorry", "statement": GOAL, "runtime": rt}))
    assert _counts(rt)["placeholder_proof"] == 3


def test_different_guards_are_counted_separately(rt):
    """The point of keying on the error code: which guard, not how many.

    A real `check_statement` is needed to make `not_the_goal` reachable --
    `declared_goal` reads the last STATEMENT_CHECK record, and with none on
    file the drift guard is deliberately a no-op. `log.set_goal` sets the
    CURRENT goal, which is a different thing, and an earlier draft of this
    test conflated them and quietly exercised only one guard.
    """
    from math_v2.core import proving

    async def ok(source):
        return LeanResult(LeanOutcome.COMPILED, "")

    run(proving.check_statement(rt.context.workdir, GOAL, ok))

    run(proving_tools.try_proof.ainvoke(
        {"proof": "by sorry", "statement": GOAL, "runtime": rt}))
    run(proving_tools.try_proof.ainvoke(
        {"proof": "by rfl", "statement": "theorem other : 1 = 1", "runtime": rt}))

    counts = _counts(rt)
    assert counts.get("placeholder_proof") == 1, counts
    assert counts.get("not_the_goal") == 1, counts


def test_a_real_compilation_is_not_counted(rt):
    """Only refusals. A compiled rejection is an attempt, not a refusal, and
    counting it would make the number meaningless."""
    run(proving_tools.try_proof.ainvoke(
        {"proof": "by exact Nat.add_zero n", "statement": GOAL, "runtime": rt}))
    assert _counts(rt) == {}


def test_the_proof_log_stays_clean(rt):
    """The property that makes a counter the right shape. A refusal must not
    become an attempt in the log, because `goal_attempts`, `mean_attempts` and
    the decomposition redirect itself are all computed from those records."""
    for _ in range(3):
        run(proving_tools.try_proof.ainvoke(
            {"proof": "by sorry", "statement": GOAL, "runtime": rt}))

    assert _counts(rt)["placeholder_proof"] == 3
    assert log.records(rt.context.workdir, log.PROOF) == [], (
        "refusals leaked into the proof log and will inflate every "
        "attempt-based metric"
    )


def test_recording_never_raises(tmp_path):
    """Wrapped like `terminate`: a lost count is a worse measurement, a raised
    exception is a lost goal."""
    budget.record_refusal(str(tmp_path / "does-not-exist"), "whatever")
    budget.record_refusal(str(tmp_path), None)
    budget.record_refusal(str(tmp_path), "")


# ------------------------------------------------ out to the results file
def test_it_reaches_the_results_file(rt):
    from domain.proof import ProofRun
    from eval.proof_dataset import Tier
    from eval.proof_metrics import result_from
    from math_v2 import harness

    run(proving_tools.try_proof.ainvoke(
        {"proof": "by sorry", "statement": GOAL, "runtime": rt}))

    class Goal:
        id, area, tier = "g", "number theory", Tier.PROOFNET

    workdir = rt.context.workdir
    proof_run = harness._to_proof_run(ProofRun(goal="q"), workdir, prose="",
                                      seconds=0.0)
    assert proof_run.telemetry.refusals == {"placeholder_proof": 1}
    assert result_from(Goal(), proof_run).refusals == {"placeholder_proof": 1}


def test_a_resume_carries_the_counts(tmp_path):
    """Free again, via `rehydrate` reading `dataclasses.fields`."""
    from eval.proof_dataset import Tier
    from eval.proof_metrics import ProofOutcome, ProofResult
    from scripts.evaluate_proofs import completed, save

    original = ProofResult(goal_id="g", area="x", tier=Tier.PROOFNET,
                           outcome=ProofOutcome.NOT_PROVED,
                           refusals={"decompose_first": 2})
    out = tmp_path / "run.json"
    save([original], {}, out)
    assert completed(True, out)[0].refusals == {"decompose_first": 2}


# ------------------------------------------------- the consolidation
def test_every_budget_field_survives_a_read(tmp_path):
    """`_FIELDS` is derived from `_fresh()` now, because it used to be a
    hand-written tuple naming the same keys a fourth time -- and a key missing
    from it is silently dropped on every read. That is the results-file bug
    (7c9393f) one layer down."""
    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.record_refusal(workdir, "decompose_first")

    fresh = budget._fresh()
    state = budget.read(workdir)
    missing = [k for k in fresh if k not in state]
    assert not missing, f"dropped on read: {missing}"
    assert state["refusals"] == {"decompose_first": 1}
