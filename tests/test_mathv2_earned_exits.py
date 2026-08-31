"""Two holes the second ProofNet run exposed. Offline.

MEASURED, from the rerun after the search cap started binding:

    goal            searches  attempts  outcome             seconds
    exercise_1_13a         3         0  suspect statement        83
    exercise_1_13c         3         0  suspect statement       207
    exercise_1_19b         0         0  not formalised         1032
    exercise_1_26          3         1  suspect statement       202

The search fix worked — 6,5,7,5 executed searches became 3,3,0,3. But
`genuinely_tested` came out 0 of 4, because the budget that stopped going to
retrieval went to QUITTING rather than to the compiler, and because one goal
overran a 300s budget by 244%.

1. `statement_suspect` ended three runs with zero proofs compiled. Every other
   terminal claim in this system is checked against recorded tool executions;
   that one was checked against prose. So it was the cheapest exit available
   and the agent took it.

   The mathematics was RIGHT — Stein & Shakarchi Ch.1 Ex.13 is stated over a
   region, open AND connected, and the ProofNet port has only `IsOpen`, so
   without connectedness f can be a different constant on each component. That
   is one of the 118/371 broken rows arXiv 2406.07222 documents. The rule below
   is therefore about EFFORT, not correctness: we cannot check the mathematics,
   and blocking the report would suppress a real finding.

2. The reserve was a constant. `reserve()` promised to refuse a compile that
   cannot finish inside the budget, and reserved 60s where a compile costs
   ~340s — so the promise was true only on the hardware the number came from.
"""

import asyncio
import time

import pytest

from math_v2.context import MathContext
from math_v2.core import budget, log, verdict
from math_v2.tools.control import finish

STATEMENT = "theorem mra_goal : 2 + 2 = 4"


def run(coro):
    return asyncio.run(coro)


def runtime_for(workdir):
    from langchain.tools import ToolRuntime

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


def call_finish(workdir, **kwargs):
    kwargs.setdefault("summary", "Omega is not assumed connected, so this is false.")
    return run(finish.ainvoke({**kwargs, "runtime": runtime_for(workdir)}))


def record_attempt(workdir, proof="by simp", status=None):
    log.append(str(workdir), log.Record(kind=log.PROOF, statement=STATEMENT,
                                        proof=proof,
                                        status=status or log.UNKNOWN))


def record_refutation(workdir, proof="by simp", status=None):
    """A counterexample PUT to the compiler. Passing is not required — the
    second gate asks that it was tried, not that it worked."""
    log.append(str(workdir), log.Record(kind=log.REFUTATION,
                                        statement="theorem t : ¬ (2 = 3)",
                                        proof=proof,
                                        status=status or log.FALSE))


# ---------------------------------------- 1. the suspect exit must be earned
def test_calling_a_statement_suspect_without_proving_anything_is_refused(tmp_path):
    """THE fix. exercise_1_13a: one statement check, three searches, zero
    proofs compiled, run over."""
    result = call_finish(tmp_path, outcome="statement_suspect")

    assert result["accepted"] is False
    assert result["error"] == "suspect_unearned"
    assert "try_proof" in result["message"]


def test_a_statement_check_does_not_count_as_having_tried(tmp_path):
    """It compiles the SIGNATURE with a placeholder. It answers "can Lean parse
    this", which is not an attempt at the mathematics."""
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=STATEMENT,
                                         proof="", status=log.UNKNOWN))

    assert verdict.attempted_a_proof(str(tmp_path)) is False
    assert call_finish(tmp_path, outcome="statement_suspect")["accepted"] is False


def test_a_proof_attempt_alone_no_longer_earns_the_report(tmp_path):
    """TIGHTENED after the 4-goal run. All three suspect reports came with a
    counterexample written out in prose, and `try_refutation` was never called
    once — the tool existed, the prompt described it, and nothing in the
    control flow ever put the model in front of it."""
    record_attempt(tmp_path)

    result = call_finish(tmp_path, outcome="statement_suspect")

    assert result["accepted"] is False
    assert result["error"] == "suspect_unearned"
    assert "try_refutation" in result["message"]


def test_a_tried_and_failed_refutation_earns_the_report(tmp_path):
    """The agent is often RIGHT — 31.8% of ProofNet's Lean statements are
    broken. This must stay possible, just not free. The requirement is that the
    counterexample reached the compiler, NOT that it compiled."""
    record_attempt(tmp_path)
    record_refutation(tmp_path)

    result = call_finish(tmp_path, outcome="statement_suspect")

    assert result["accepted"] is True
    assert any("suspect statement" in e for e in log.read(str(tmp_path))["trace"])


def test_the_second_gate_hands_over_the_negation_to_prove(tmp_path):
    """A refusal that only says "try harder" burns the remaining turns. This one
    carries the statement the model would have had to write."""
    record_attempt(tmp_path)
    log.set_goal(str(tmp_path), STATEMENT)
    # `suspect_refusal` now builds the offered negation from `declared_goal`,
    # which is derived from STATEMENT_CHECK records rather than the `goal`
    # field `set_goal` writes — so a declaration has to be seeded here too.
    log.append(str(tmp_path), log.Record(kind=log.STATEMENT_CHECK,
                                         statement=STATEMENT, status=log.TRUE))

    message = call_finish(tmp_path, outcome="statement_suspect")["message"]

    assert "try_refutation" in message
    assert "¬" in message, "the negation itself was not offered"


def test_the_report_is_still_never_a_verdict(tmp_path):
    """Earning it does not make it true. The guard must still say unproved."""
    record_attempt(tmp_path)
    call_finish(tmp_path, outcome="statement_suspect")

    decision = verdict.proof_verdict(str(tmp_path), STATEMENT)
    assert decision["outcome"] != verdict.PROVED
    assert verdict.refuse(verdict.PROVED, decision), "a report became a proof"


def test_nothing_is_written_to_the_trace_when_the_report_is_refused(tmp_path):
    """Otherwise the evaluator would classify it SUSPECT_STATEMENT anyway and
    the guard would be decorative."""
    call_finish(tmp_path, outcome="statement_suspect")

    assert not any("suspect statement" in e
                   for e in log.read(str(tmp_path))["trace"])


def test_reporting_not_proved_honestly_needs_no_evidence(tmp_path):
    """Admitting failure must always be free, or the agent is cornered."""
    assert call_finish(tmp_path, outcome="not_proved")["accepted"] is True


def test_a_statement_that_never_elaborated_can_still_be_reported(tmp_path):
    """exercise_1_19b. There is nothing to attempt a proof OF."""
    assert call_finish(tmp_path, outcome="not_formalized")["accepted"] is True


def test_the_refusal_names_the_tool_that_would_satisfy_it(tmp_path):
    """A refusal the model cannot act on just burns the remaining turns."""
    message = call_finish(tmp_path, outcome="statement_suspect")["message"]

    assert "try_proof" in message


# -------------------------------------------- 2. the reserve is now measured
def test_the_reserve_starts_at_the_seed_with_nothing_measured():
    assert budget.reserve({}) == min(budget.LEAN_RESERVE_SECONDS,
                                     budget.MAX_SECONDS * budget.MAX_RESERVE_FRACTION)


def test_a_slow_compile_raises_the_reserve(monkeypatch):
    """THE fix. 60s reserved, 340s actual, 1032s spent against 300s."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 3600.0)

    assert budget.reserve({"slowest_lean": 340.0}) == 340.0


def test_the_reserve_can_never_eat_the_budget_it_protects(monkeypatch):
    """Cutting the other way. A 340s measurement against a 300s budget would
    refuse every compile forever — an overshoot turned into a run that does
    nothing at all."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 300.0)

    assert budget.reserve({"slowest_lean": 340.0}) == 75.0


def test_only_the_slowest_compile_is_kept(tmp_path):
    """An average would let a fast compile talk us into starting a slow one."""
    workdir = str(tmp_path)
    budget.reset(workdir)

    budget.record_lean_seconds(workdir, 12.0)
    budget.record_lean_seconds(workdir, 340.0)
    budget.record_lean_seconds(workdir, 8.0)

    assert budget.read(workdir)["slowest_lean"] == 340.0


def test_a_measured_compile_time_actually_blocks_the_next_compile(tmp_path, monkeypatch):
    """End to end: the measurement has to reach `_over`, not just be stored."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 1000.0)
    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.record_lean_seconds(workdir, 240.0)      # a real Windows compile

    data = log.read(workdir)
    data["budget"]["started"] = time.time() - 800   # 200s left, under 240
    log._write(workdir, data)

    stop = budget.spend(workdir, lean=True)

    assert stop is not None, "a compile that cannot finish was started anyway"
    assert "slowest seen here" in stop["message"]


def test_the_same_moment_still_allows_a_search(tmp_path, monkeypatch):
    """The reserve bounds COMPILING. A search costs milliseconds and must not
    be refused by a rule about compile time."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 1000.0)
    monkeypatch.setattr(budget, "SEARCH_DEADLINE_FRACTION", 1.0)
    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.record_lean_seconds(workdir, 240.0)

    data = log.read(workdir)
    data["budget"]["started"] = time.time() - 800
    log._write(workdir, data)

    assert budget.spend(workdir, search=True) is None


def test_timing_never_breaks_a_compile():
    """It runs in a `finally` around the compiler. A failure here must not
    become a Lean failure."""
    budget.record_lean_seconds("/nonexistent/path/that/cannot/be/written", 5.0)


def test_a_fast_machine_is_not_penalised(tmp_path, monkeypatch):
    """20s compiles must not inherit the seed as a floor for BLOCKING — the
    seed is a floor on the reserve, which is deliberate, but it must stay
    small against a real budget."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 900.0)

    assert budget.reserve({"slowest_lean": 20.0}) == budget.LEAN_RESERVE_SECONDS


def test_the_seed_clears_both_measured_cold_imports():
    """MEASURED, twice, elsewhere in this codebase: a cold `import Mathlib`
    costs 40.5s steady-state and 116s cold on Windows (`_repl.py`'s own
    docstring; `_repl.START_TIMEOUT` is set from the same number). The seed
    protects exactly the FIRST compile of a run, before anything about THIS
    machine has been measured — so it must clear the higher of the two, not
    just the lower. A seed at the old value (60) failed this by nearly half."""
    assert budget.LEAN_RESERVE_SECONDS >= 116.0
