"""The three changes the first ProofNet pilot justified. Offline.

MEASURED, from four traces:

    goal              tool calls   seconds   s/call   searches   real proofs
    exercise_1_13a             8       257       32          6             1
    exercise_1_13c             8       283       35          5             0
    exercise_1_19b            10       248       25          7             1
    exercise_1_26              9       298       33          5             2

31 seconds per tool call, because the clock is bought by MODEL LATENCY and not
by tool work. A 300s budget therefore buys about ten turns however high
MAX_AGENT_STEPS is set — and searching took 23 of the 35 available, 66%, while
returning things like `Std.Sat.AIG.getConstant` for the query "constant".

So: stop paying for machinery, close retrieval at the halfway mark, and stop
reporting four different failures as one number.
"""

import asyncio
import time

import pytest

from domain.proof import ProofRun
from eval.proof_dataset import Goal, Tier
from eval.proof_metrics import ProofOutcome, classify, result_from, summarize
from math_v2.context import MathContext
from math_v2.core import budget, log, retrieval
from math_v2.tools import retrieval as retrieval_tool
from math_v2.tools.control import finish
from retrieval.loogle import Premise

GOAL = Goal(id="g", area="a", goal="q", tier=Tier.PROOFNET)


def run(coro):
    return asyncio.run(coro)


def runtime_for(workdir):
    from langchain.tools import ToolRuntime

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


# ------------------------------------------------- 1. retrieval is filtered
def test_compiler_internals_are_dropped_from_results():
    """A search for "constant" returned eight of these and nothing usable."""
    found = [
        Premise(name="Std.Sat.AIG.getConstant", module="Std.Sat.AIG"),
        Premise(name="Lean.ConstantInfo", module="Lean"),
        Premise(name="Lean.instBEqConstantVal", module="Lean"),
        Premise(name="isCyclic_of_prime_card", module="Mathlib.GroupTheory"),
    ]
    kept, dropped = retrieval.drop_noise(found)

    assert [p.name for p in kept] == ["isCyclic_of_prime_card"]
    assert dropped == 3


def test_the_filter_never_empties_a_result_list():
    """A filter that can return nothing turns a poor search into a silent one,
    and the agent cannot tell those apart."""
    only_noise = [
        Premise(name="Lean.defaultMaxRecDepth", module="Lean"),
        Premise(name="Lean.Macro.MethodsRef", module="Lean.Macro"),
    ]
    kept, dropped = retrieval.drop_noise(only_noise)

    assert len(kept) == 2, "the agent would have seen an empty search"
    assert dropped == 0


def test_real_mathlib_results_are_untouched():
    """The seven near-mathlib goals must behave exactly as before."""
    real = [
        Premise(name="Nat.exists_infinite_primes", module="Mathlib.Data.Nat.Prime.Infinite"),
        Premise(name="irrational_sqrt_two", module="Mathlib.Analysis.Irrational"),
        Premise(name="Module.Basis.exists_basis", module="Mathlib.LinearAlgebra.Basis"),
    ]
    kept, dropped = retrieval.drop_noise(real)

    assert kept == real
    assert dropped == 0


def test_tactic_framework_names_are_machinery_too():
    """`Mathlib.TacticAnalysis.ComplexConfig` came back for the query "Complex"."""
    found = [Premise(name="Mathlib.TacticAnalysis.ComplexConfig",
                     module="Mathlib.TacticAnalysis")]
    assert retrieval.is_noise(found[0])


def test_the_agent_is_told_when_results_were_hidden(tmp_path):
    """Silently shortening a list would look like a weaker search."""
    class Search:
        def search_with_suggestions(self, query, limit=None):
            return [
                Premise(name="Lean.ConstantInfo", module="Lean"),
                Premise(name="Nat.Prime", module="Mathlib.Data.Nat.Prime"),
            ], []

    result = retrieval.search_mathlib(str(tmp_path), "constant", Search())

    assert "1 compiler-internal result(s) hidden" in result["message"]
    assert "too generic" in result["message"]


# --------------------------------------------- 2. retrieval closes early
def test_search_is_refused_once_half_the_clock_is_gone(tmp_path, monkeypatch):
    """THE fix for the systemic issue. Every search after the halfway mark in
    the pilot produced nothing usable and consumed a turn the compiler needed."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 300.0)
    workdir = str(tmp_path)
    budget.reset(workdir)

    # Rewind the clock to 200s spent — past half of 300.
    data = log.read(workdir)
    data["budget"]["started"] = time.time() - 200
    log._write(workdir, data)

    stop = budget.spend(workdir, search=True)

    assert stop is not None
    assert stop["error"] == budget.REDIRECT
    assert "SEARCH IS CLOSED" in stop["message"]
    assert stop["terminated"] is False, "the run is not over, only searching"


def test_compiling_is_still_allowed_after_the_search_deadline(tmp_path, monkeypatch):
    """The point is to REDIRECT the clock to Lean, not to end the run."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 300.0)
    workdir = str(tmp_path)
    budget.reset(workdir)

    data = log.read(workdir)
    data["budget"]["started"] = time.time() - 200
    log._write(workdir, data)

    assert budget.spend(workdir, lean=True) is None, "compiling was blocked too"


def test_searching_early_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "MAX_SECONDS", 300.0)
    budget.reset(str(tmp_path))

    assert budget.spend(str(tmp_path), search=True) is None


def test_the_deadline_is_configurable_for_an_ablation(monkeypatch):
    assert 0 < budget.SEARCH_DEADLINE_FRACTION <= 1


# ------------------------------- 2b. the cap the pilot proved was not binding
def searches_allowed(workdir, monkeypatch, *, before, **charge):
    """How many searches run given `before` searches, then one charged call."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 1e9)
    budget.reset(workdir)
    for _ in range(before):
        budget.spend(workdir, search=True)
    budget.spend(workdir, **charge)
    ran = 0
    while budget.spend(workdir, search=True) is None:
        ran += 1
    return ran


def test_a_statement_check_does_not_buy_three_more_searches(tmp_path, monkeypatch):
    """THE bug the four ProofNet traces show, and the one my earlier isolated
    test could not see because it never made a second Lean call.

    `searches_since_compile` was reset by every Lean call, so the cap bounded
    RUNS of searching rather than searching. Two or three `check_statement`
    calls per goal bought three more queries each: 5, 5, 6 and 7 searches
    executed under a cap of 3.
    """
    allowed = searches_allowed(str(tmp_path), monkeypatch,
                               before=budget.MAX_CONSECUTIVE_SEARCHES,
                               lean=True)

    assert allowed == 0, (
        "a statement check refilled the search allowance — this is how "
        "exercise_1_13a executed six searches under a cap of three"
    )


def test_a_rejected_proof_does_buy_more_searches(tmp_path, monkeypatch):
    """The other half. A proof attempt returns a GOAL STATE, so the next query
    can be aimed at what actually remains. That is targeted retrieval and it is
    exactly what the allowance is for — refusing it would make the agent search
    blindly once and then never again."""
    allowed = searches_allowed(str(tmp_path), monkeypatch,
                               before=budget.MAX_CONSECUTIVE_SEARCHES,
                               lean=True, goal_state=True)

    assert allowed == budget.MAX_CONSECUTIVE_SEARCHES


def test_the_pilots_own_sequence_is_now_bounded(tmp_path, monkeypatch):
    """exercise_1_13a as it actually ran: statement check, tactic ladder, then
    the agent searching until something stopped it. Six searches got through."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 1e9)
    workdir = str(tmp_path)
    budget.reset(workdir)

    budget.spend(workdir, lean=True)                     # check_statement
    budget.spend(workdir, lean=True, goal_state=True)    # try_standard_tactics
    ran = 0
    while budget.spend(workdir, search=True) is None:
        ran += 1

    assert ran == budget.MAX_CONSECUTIVE_SEARCHES, f"{ran} searches, not 3"


def test_the_tactic_ladder_counts_as_feedback(tmp_path, monkeypatch):
    """It reports which of ~30 tactics failed and how, on the real goal."""
    import inspect

    from math_v2.tools import proving as proving_tools

    src = inspect.getsource(proving_tools)
    charges = src.count("_charge(runtime, lean=True, goal_state=True)")
    assert charges == 4, (
        "try_proof, try_lemma, try_skeleton and try_standard_tactics return a "
        f"goal state; found {charges} marked so"
    )
    assert src.count("_charge(runtime, lean=True)\n") == 1, (
        "only check_statement should charge Lean without a goal state"
    )


# ------------------------------------ 3. four failures, four categories
def proof_run(*, statement="theorem t : True", ok=True, proved=False, trace=()):
    run = ProofRun(goal="q", statement=statement, statement_ok=ok)
    run.trace.extend(trace)
    if proved:
        run.proof = "by norm_num"
        from domain.verdict import Verdict, VerificationStatus

        run.verdict = Verdict(VerificationStatus.TRUE, "lean", "accepted")
    return run


def test_a_statement_that_never_elaborated_is_not_a_proving_failure():
    """exercise_1_19b: `Complex.abs` no longer exists in Lean v4.33."""
    assert classify(proof_run(ok=False)) is ProofOutcome.NOT_FORMALIZED


def test_a_suspect_statement_is_recorded_as_the_agents_report():
    """exercise_1_13c: Ω is not assumed connected, so the claim is false."""
    run = proof_run(trace=["suspect statement: Omega is not assumed connected"])
    assert classify(run) is ProofOutcome.SUSPECT_STATEMENT


def test_running_out_of_clock_is_not_the_same_as_failing_to_prove():
    """exercise_1_26: decomposed correctly, ran out with sub-lemmas unproved."""
    run = proof_run(trace=["stopped early: time budget spent (300s)"])
    assert classify(run) is ProofOutcome.EXHAUSTED


def test_a_real_failure_is_still_a_real_failure():
    """exercise_1_13a: elaborated, had the budget, found nothing."""
    assert classify(proof_run()) is ProofOutcome.NOT_PROVED


def test_a_proof_outranks_every_other_category():
    """A compiler acceptance is a fact; nothing downgrades it."""
    run = proof_run(proved=True, trace=["stopped early: time budget spent",
                                        "suspect statement: hmm"])
    assert classify(run) is ProofOutcome.PROVED


def test_the_four_pilot_goals_no_longer_collapse_into_one_number():
    """THE reporting fix. 0/4 said the prover failed four times; it was
    genuinely tested once."""
    results = [
        result_from(GOAL, proof_run(ok=False)),                                   # 1_19b
        result_from(GOAL, proof_run(trace=["suspect statement: not connected"])),  # 1_13c
        result_from(GOAL, proof_run(trace=["stopped early: time budget spent"])),  # 1_26
        result_from(GOAL, proof_run()),                                            # 1_13a
    ]
    summary = summarize(results)

    assert summary["not_formalized"] == 1
    assert summary["suspect_statements"] == 1
    assert summary["exhausted"] == 1
    assert summary["genuinely_tested"] == 1, (
        "the prover was put to the test once, not four times"
    )
    assert summary["proof_rate_of_tested"] == 0.0


def test_the_categories_appear_in_the_rendered_report():
    from eval.proof_metrics import render

    text = render(summarize([result_from(GOAL, proof_run(ok=False))]))

    for line in ("statement not elaborable", "statement suspect",
                 "budget exhausted", "genuinely tested"):
        assert line in text, line


# ---------------------------------------- finish records, never concludes
def test_reporting_a_suspect_statement_is_allowed_and_recorded(tmp_path):
    """Still allowed — the agent is often right, ProofNet being 31.8% broken —
    but no longer FREE. The rerun showed three of four goals taking this exit
    with zero proofs compiled, so it now costs one rejected attempt, the same
    as trying. See tests/test_mathv2_earned_exits.py."""
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement="theorem t : True",
                                         proof="by simp", status=log.UNKNOWN))

    result = run(finish.ainvoke({
        "summary": "Omega is not assumed connected, so this is false as stated.",
        "outcome": "statement_suspect",
        "runtime": runtime_for(tmp_path),
    }))

    assert result["accepted"] is True
    assert any("suspect statement" in entry
               for entry in log.read(str(tmp_path))["trace"])


def test_calling_a_statement_suspect_never_establishes_anything(tmp_path):
    """It is a REPORT. The guard still says the goal is unproved."""
    from math_v2.core import verdict as verdicts

    run(finish.ainvoke({
        "summary": "looks false", "outcome": "statement_suspect",
        "runtime": runtime_for(tmp_path),
    }))

    decision = verdicts.proof_verdict(str(tmp_path), "theorem t : True")
    assert decision["outcome"] != verdicts.PROVED
    assert verdicts.refuse(verdicts.PROVED, decision), "a report became a proof"


# ------------------------------------- the seven near-mathlib goals are safe
def test_the_curated_benchmark_still_classifies_the_way_it_did():
    """A proved goal and an ordinary failure must be unaffected by all of this."""
    assert classify(proof_run(proved=True)) is ProofOutcome.PROVED
    assert classify(proof_run()) is ProofOutcome.NOT_PROVED
    assert classify(proof_run(statement="")) is ProofOutcome.NOT_FORMALIZED
