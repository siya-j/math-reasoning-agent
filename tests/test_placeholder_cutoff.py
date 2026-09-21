"""Repeated `sorry` is the agent saying it is stuck. Believe it.

MEASURED on `eval/results/test-186.json`, 186 goals:

    submitted `sorry` at least once   n=90   proved 29%
    never did                         n=96   proved 92%

and of the 21 goals that submitted three or more, NOT ONE proved. They cost
13.6M input tokens -- 24% of the run -- after saying they were stuck. At two
submissions four goals still proved, which is why the cut is at three.

THE CLASSIFICATION IS THE PART THAT MATTERS. The stop is written through the
same state field as a wall-clock stop, so it arrives at
`proof_metrics.classify` looking identical, and EXHAUSTED leaves the proof-rate
denominator. A goal that gave up is a proving failure, not a defective
statement: scoring these as EXHAUSTED would have removed 21 real failures from
test-186 and inflated the rate. These tests exist because that mistake is
invisible in a green suite and shows up only as a suspiciously good number.
"""

import pytest

from domain.proof import ProofRun, Verdict, VerificationStatus
from eval.proof_metrics import ProofOutcome, classify
from math_v2.core import budget, proving


@pytest.fixture
def workdir(tmp_path):
    return str(tmp_path)


def refuse(workdir, times, code=budget.PLACEHOLDER_CODE):
    for _ in range(times):
        budget.record_refusal(workdir, code)
    return budget.summary(workdir)


# ------------------------------------------------------------------- the cut
def test_two_submissions_are_survivable(workdir):
    """Four goals in test-186 proved after a second submission."""
    spent = refuse(workdir, 2)

    assert spent["refusals"][budget.PLACEHOLDER_CODE] == 2
    assert not spent.get("terminated_early"), "cut one submission too early"


def test_the_third_submission_ends_the_goal(workdir):
    spent = refuse(workdir, 3)

    assert spent["refusals"][budget.PLACEHOLDER_CODE] == 3
    assert spent.get("terminated_early"), "the goal kept running after giving up"
    assert "gave up" in spent["reason"]


def test_a_different_refusal_does_not_count_towards_it(workdir):
    """`assemble_first` and friends are redirects, not admissions."""
    spent = refuse(workdir, 5, code="assemble_first")

    assert not spent.get("terminated_early")


# ------------------------------------- the part that would inflate the rate
def stopped(reason):
    run = ProofRun(goal="g")
    run.statement = "theorem t (n : Nat) : n + 0 = n"
    run.verdict = Verdict(VerificationStatus.UNKNOWN, "prover", "")
    run.trace = [f"stopped early: {reason}"]
    return run


def test_giving_up_stays_in_the_denominator():
    """NOT_PROVED, not EXHAUSTED. This is a proving failure."""
    assert classify(stopped("gave up: 3 placeholder submissions")) \
        is ProofOutcome.NOT_PROVED


def test_a_real_budget_stop_is_still_exhausted():
    """The existing behaviour must be untouched: the budget really did run
    out, so the goal was not refused by the mathematics."""
    assert classify(stopped("budget spent")) is ProofOutcome.EXHAUSTED
    assert classify(stopped("time budget spent (383s of 300s)")) \
        is ProofOutcome.EXHAUSTED


# --------------------------------------------------------------- the coupling
def test_the_code_matches_the_refusal_that_produces_it():
    """A silent rename in `_placeholder_refusal` would disable the cutoff.

    Nothing else connects them -- the budget matches on a string -- so the
    failure mode is that the cut quietly stops happening and the only symptom
    is the bill.
    """
    assert proving._placeholder_refusal()["error"] == budget.PLACEHOLDER_CODE
