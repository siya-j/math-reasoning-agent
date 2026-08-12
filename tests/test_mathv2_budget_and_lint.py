"""The two audit fixes: budget enforcement, and the faithfulness lint.

Both are tested THROUGH THE REAL TOOLS, not against the helpers in isolation.
The audit's finding was that the budget existed as a concept and was not wired
into the agent path, so a test that only exercises `budget.spend` would repeat
the exact mistake it is meant to catch.

`test_no_lean_call_is_made_once_the_compile_budget_is_spent` and
`test_a_compiled_proof_of_the_wrong_theorem_is_refused` are the two that carry
the fixes.
"""

import asyncio
import time

import pytest

from math_v2 import _aura
from math_v2.context import MathContext
from math_v2.core import budget, log, verdict
from math_v2.tools import proving as proving_tools
from math_v2.tools import retrieval, symbolic
from math_v2.tools.control import finish
from math_v2.tools.proving import (
    check_statement,
    try_lemma,
    try_proof,
    try_skeleton,
    try_standard_tactics,
)

STATEMENT = "theorem mra_goal : 2 + 2 = 4"


def run(coro):
    return asyncio.run(coro)


def runtime_for(workdir):
    from langchain.tools import ToolRuntime

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


@pytest.fixture
def lean_calls(monkeypatch):
    """Counts every dispatch that actually reached the compiler."""
    calls = []

    async def fake(source):
        calls.append(source)
        from verifiers.lean_runner import LeanOutcome, LeanResult

        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda workdir: fake)
    return calls


@pytest.fixture
def search_calls(monkeypatch):
    calls = []

    class Search:
        def search_with_suggestions(self, query, limit=None):
            calls.append(query)
            return [], []

    monkeypatch.setattr(retrieval, "get_search", lambda: Search())
    return calls


@pytest.fixture
def symbolic_calls(monkeypatch):
    calls = []

    def dispatcher(workdir):
        async def dispatch(op, args):
            calls.append(op)
            return {"ok": True, "outputs": {"status": "true", "detail": ""}}

        return dispatch

    monkeypatch.setattr(symbolic, "worker_dispatch", dispatcher)
    return calls


# ------------------------------------------------------- hard limits, wired
def test_no_lean_call_is_made_once_the_compile_budget_is_spent(
        tmp_path, monkeypatch, lean_calls):
    """THE fix. Not "the model is asked to stop" — the work does not happen."""
    monkeypatch.setattr(budget, "MAX_LEAN_CALLS", 2)
    rt = runtime_for(tmp_path)

    for _ in range(6):
        run(try_proof.ainvoke({"proof": "trivial", "statement": STATEMENT,
                               "runtime": rt}))

    assert len(lean_calls) == 2, "the compiler was invoked past its budget"


def test_the_tool_budget_bounds_every_kind_of_call(
        tmp_path, monkeypatch, lean_calls, search_calls):
    monkeypatch.setattr(budget, "MAX_TOOL_CALLS", 3)
    rt = runtime_for(tmp_path)

    for _ in range(5):
        run(search_mathlib_call(rt))
        run(try_proof.ainvoke({"proof": "trivial", "statement": STATEMENT,
                               "runtime": rt}))

    assert len(lean_calls) + len(search_calls) == 3


def search_mathlib_call(rt):
    return retrieval.search_mathlib.ainvoke({"query": "Nat.Prime", "runtime": rt})


def test_the_search_budget_stops_searching(tmp_path, monkeypatch, search_calls):
    monkeypatch.setattr(budget, "MAX_SEARCHES", 2)
    monkeypatch.setattr(budget, "MAX_CONSECUTIVE_SEARCHES", 99)
    rt = runtime_for(tmp_path)

    for _ in range(5):
        run(search_mathlib_call(rt))

    assert len(search_calls) == 2


def test_the_symbolic_budget_stops_computing(tmp_path, monkeypatch, symbolic_calls):
    monkeypatch.setattr(budget, "MAX_SYMBOLIC_CALLS", 2)
    rt = runtime_for(tmp_path)

    for _ in range(5):
        run(symbolic.check_primality.ainvoke({"n": "7", "runtime": rt}))

    assert len(symbolic_calls) == 2


def test_the_wall_clock_stops_everything(tmp_path, monkeypatch, lean_calls):
    monkeypatch.setattr(budget, "MAX_SECONDS", -1)      # already over
    rt = runtime_for(tmp_path)

    result = run(try_proof.ainvoke({"proof": "trivial", "statement": STATEMENT,
                                    "runtime": rt}))

    assert lean_calls == [], "work was done after the time budget expired"
    assert result["error"] == budget.EXHAUSTED
    assert result["limit"] == "time"


def test_a_compile_is_not_started_when_it_cannot_finish_in_time(
        tmp_path, monkeypatch, lean_calls):
    """A compile begun near the deadline still runs a full Lean timeout past it."""
    # Reserve the whole budget, so any elapsed time at all leaves too little.
    monkeypatch.setattr(budget, "MAX_SECONDS", 100.0)
    monkeypatch.setattr(budget, "LEAN_RESERVE_SECONDS", 100.0)
    monkeypatch.setattr(budget, "MAX_RESERVE_FRACTION", 1.0)
    rt = runtime_for(tmp_path)

    result = run(try_proof.ainvoke({"proof": "trivial", "statement": STATEMENT,
                                    "runtime": rt}))
    assert lean_calls == []
    assert result["limit"] == "time"


def test_the_reservation_can_never_eat_the_budget_it_protects(monkeypatch):
    """It reserved 180s of a 300s budget, so 60% was unusable and the agent was
    refused before it ever attempted a proof. Measured, on the first real run."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 300.0)
    assert budget.reserve() == 60.0
    assert 300.0 - budget.reserve() == 240.0      # the old prover's window

    for limit in (60.0, 120.0, 300.0, 900.0):
        monkeypatch.setattr(budget, "MAX_SECONDS", limit)
        assert budget.reserve() <= limit * budget.MAX_RESERVE_FRACTION + 1e-9, limit


def test_the_stop_message_reports_what_was_SPENT_not_just_the_limit(monkeypatch):
    """It said "time budget spent (300s)" after 127s, which reads as a lie."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 300.0)

    state = dict(tool_calls=0, lean_calls=0, started=time.time() - 127)
    _, over_budget = budget._over(state, lean=False)
    assert "127s of 300s" in over_budget or over_budget == "", over_budget

    # And the reservation message names the spend and the shortfall, not the cap.
    state = dict(tool_calls=0, lean_calls=0, started=time.time() - 260)
    kind, message = budget._over(state, lean=True)
    assert kind == "time"
    assert "260s of the 300s budget used" in message, message
    assert "60s left" in message, message


def test_every_lean_tool_is_charged(tmp_path, monkeypatch, lean_calls):
    """A tool that forgot to charge would be an unbounded hole in the budget."""
    monkeypatch.setattr(budget, "MAX_LEAN_CALLS", 1)
    rt = runtime_for(tmp_path)

    calls = [
        check_statement.ainvoke({"statement": STATEMENT, "runtime": rt}),
        try_proof.ainvoke({"proof": "trivial", "runtime": rt}),
        try_standard_tactics.ainvoke({"runtime": rt}),
        try_lemma.ainvoke({"statement": "lemma h : True", "proof": "trivial",
                           "runtime": rt}),
        try_skeleton.ainvoke({"proof": "sorry", "runtime": rt}),
    ]
    for call in calls:
        run(call)

    assert len(lean_calls) == 1, "a Lean tool bypassed the budget"


# ---------------------------------------------------- stopping cleanly
def test_exhaustion_returns_a_structured_result_rather_than_raising(
        tmp_path, monkeypatch, lean_calls):
    monkeypatch.setattr(budget, "MAX_LEAN_CALLS", 0)
    rt = runtime_for(tmp_path)

    result = run(try_proof.ainvoke({"proof": "trivial", "statement": STATEMENT,
                                    "runtime": rt}))

    assert result["ok"] is False
    assert result["error"] == budget.EXHAUSTED
    assert result["limit"] == "lean"
    assert "budget" in result and "finish" in result["message"]


def test_after_grace_the_run_is_terminated_and_stays_terminated(
        tmp_path, monkeypatch, lean_calls):
    monkeypatch.setattr(budget, "MAX_LEAN_CALLS", 0)
    monkeypatch.setattr(budget, "GRACE", 1)
    budget.reset(str(tmp_path))
    rt = runtime_for(tmp_path)

    first = run(try_proof.ainvoke({"proof": "a", "runtime": rt,
                                   "statement": STATEMENT}))
    second = run(try_proof.ainvoke({"proof": "b", "runtime": rt}))
    third = run(try_proof.ainvoke({"proof": "c", "runtime": rt}))

    assert first["terminated"] is False
    assert second["terminated"] is True
    assert third["terminated"] is True
    assert lean_calls == []


def test_finish_is_never_blocked_by_the_budget(tmp_path, monkeypatch):
    """Refusing the clean exit is the one way to guarantee no verdict at all."""
    monkeypatch.setattr(budget, "MAX_TOOL_CALLS", 0)

    result = run(finish.ainvoke({"summary": "ran out", "outcome": "not_proved",
                                 "runtime": runtime_for(tmp_path)}))
    assert result["accepted"] is True


def test_finish_distinguishes_running_out_from_trying_and_failing(
        tmp_path, monkeypatch, lean_calls):
    monkeypatch.setattr(budget, "MAX_LEAN_CALLS", 0)
    monkeypatch.setattr(budget, "GRACE", 0)
    budget.reset(str(tmp_path))
    rt = runtime_for(tmp_path)

    run(try_proof.ainvoke({"proof": "a", "statement": STATEMENT, "runtime": rt}))
    result = run(finish.ainvoke({"summary": "s", "outcome": "not_proved",
                                 "runtime": rt}))

    assert any("Stopped early" in w for w in result["warnings"])
    assert result["budget"]["terminated_early"] is True


def test_the_budget_survives_a_process_boundary(tmp_path, monkeypatch, lean_calls):
    """It must outlive a turn, exactly as the proof record does."""
    monkeypatch.setattr(budget, "MAX_LEAN_CALLS", 1)
    rt = runtime_for(tmp_path)

    run(try_proof.ainvoke({"proof": "a", "statement": STATEMENT, "runtime": rt}))
    assert budget.read(str(tmp_path))["lean_calls"] == 1

    # A fresh runtime is a fresh turn; the counters must still be there.
    run(try_proof.ainvoke({"proof": "b", "runtime": runtime_for(tmp_path)}))
    assert len(lean_calls) == 1


def test_searching_is_redirected_rather_than_terminating_the_run(
        tmp_path, monkeypatch, search_calls, lean_calls):
    monkeypatch.setattr(budget, "MAX_CONSECUTIVE_SEARCHES", 2)
    rt = runtime_for(tmp_path)

    for _ in range(3):
        run(search_mathlib_call(rt))
    result = run(search_mathlib_call(rt))

    assert result["error"] == budget.REDIRECT
    assert result["terminated"] is False
    # The run is not over: compiling is still allowed.
    run(try_proof.ainvoke({"proof": "trivial", "statement": STATEMENT,
                           "runtime": rt}))
    assert len(lean_calls) == 1


# ------------------------------------------------------- faithfulness lint
def test_a_compiled_proof_of_the_wrong_theorem_is_refused(tmp_path):
    """Lean proves the STATEMENT. Nothing proves it is the question asked.

    Failures 3 and 8 in this project's log, now with a proof assistant
    attached — which makes the wrong answer more convincing, not less.
    """
    proved = "theorem t : x^2 = 4 -> x = 2 or x = -2"
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=proved,
                                         proof="by simp", status=log.TRUE))

    result = run(finish.ainvoke({
        "summary": "proved it", "outcome": "proved", "statement": proved,
        "claim": "is 2 the only solution of x^2 = 4?",
        "runtime": runtime_for(tmp_path),
    }))

    assert result["accepted"] is False
    assert result["error"] == "unfaithful_statement"
    assert "-2" in result["message"]


def test_a_faithful_proof_is_still_accepted(tmp_path):
    proved = "theorem t : 2 + 2 = 4"
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=proved,
                                         proof="by norm_num", status=log.TRUE))

    result = run(finish.ainvoke({
        "summary": "done", "outcome": "proved", "statement": proved,
        "claim": "does 2 + 2 = 4?", "runtime": runtime_for(tmp_path),
    }))
    assert result["accepted"] is True


def test_a_statement_with_no_numbers_is_not_flagged(tmp_path):
    """The lint is arithmetic and narrow; it must not fire on abstract claims."""
    proved = "theorem t (G : Type*) [Group G] : IsCyclic G"
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=proved,
                                         proof="by exact foo", status=log.TRUE))

    result = run(finish.ainvoke({
        "summary": "done", "outcome": "proved", "statement": proved,
        "claim": "is a group of prime order cyclic?",
        "runtime": runtime_for(tmp_path),
    }))
    assert result["accepted"] is True


def test_the_lint_is_skipped_when_no_claim_is_given(tmp_path):
    """It compares against the question; with none there is nothing to compare."""
    proved = "theorem t : x = 7"
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=proved,
                                         proof="by simp", status=log.TRUE))

    result = run(finish.ainvoke({
        "summary": "done", "outcome": "proved", "statement": proved,
        "runtime": runtime_for(tmp_path),
    }))
    assert result["accepted"] is True


def test_the_lint_shares_one_implementation_with_the_old_guard():
    """A second copy would drift. Same function, two callers."""
    from pipeline import faithfulness

    assert verdict.faithfulness_failure.__module__ == "math_v2.core.verdict"
    assert faithfulness.unsupported_in("uses 5", "mentions nothing") == ["5"]


# -------------------------------------------- anti-cheat still in force
#
# These patch the DISPATCH, not `lean_runner`. The anti-cheat lives inside
# `_util._classify`, so stubbing `lean_runner` would remove the very code under
# test — which the first version of these tests did, and passed a proof
# containing `sorry`.
import dataclasses


@dataclasses.dataclass
class _Spec:
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
class _Result:
    ok: bool = True
    returncode: int = 0
    stdout: str = ""
    stdout_path: str = ""
    stderr_path: str = ""


def compiler_says_yes(monkeypatch):
    """Lean accepts everything. Only our own checks can now reject a proof."""
    monkeypatch.setattr(_aura, "CommandSpec", _Spec)
    monkeypatch.setattr(_aura, "Resources", None)

    async def ok(spec):
        return _Result(ok=True, stdout="")

    monkeypatch.setattr(_aura, "run", ok)


def test_a_proof_that_compiles_via_sorry_is_not_accepted(tmp_path, monkeypatch):
    """Preserved from before: `sorry` compiles and proves nothing."""
    compiler_says_yes(monkeypatch)
    rt = runtime_for(tmp_path)

    attempt = run(try_proof.ainvoke({"proof": "by sorry", "statement": STATEMENT,
                                     "runtime": rt}))
    assert attempt["outputs"]["accepted"] is False, "`sorry` was accepted"

    result = run(finish.ainvoke({"summary": "s", "outcome": "proved",
                                 "statement": STATEMENT, "runtime": rt}))
    assert result["accepted"] is False


def test_a_proof_that_compiles_via_an_axiom_is_not_accepted(tmp_path, monkeypatch):
    compiler_says_yes(monkeypatch)
    rt = runtime_for(tmp_path)
    statement = "axiom cheat : False\ntheorem mra_goal : 2 + 2 = 5"

    attempt = run(try_proof.ainvoke({"proof": "exact absurd cheat",
                                     "statement": statement, "runtime": rt}))
    assert attempt["outputs"]["accepted"] is False, "an axiom was accepted"

    result = run(finish.ainvoke({"summary": "s", "outcome": "proved",
                                 "statement": statement, "runtime": rt}))
    assert result["accepted"] is False


def test_a_suggestion_tactic_is_not_accepted(tmp_path, monkeypatch):
    """`exact?` reports candidates rather than committing to a proof."""
    compiler_says_yes(monkeypatch)
    rt = runtime_for(tmp_path)

    attempt = run(try_proof.ainvoke({"proof": "by exact?", "statement": STATEMENT,
                                     "runtime": rt}))
    assert attempt["outputs"]["accepted"] is False


def test_an_honest_proof_still_passes_the_anticheat(tmp_path, monkeypatch):
    """The checks must reject cheating, not everything."""
    compiler_says_yes(monkeypatch)
    rt = runtime_for(tmp_path)

    attempt = run(try_proof.ainvoke({"proof": "by norm_num", "statement": STATEMENT,
                                     "runtime": rt}))
    assert attempt["outputs"]["accepted"] is True
