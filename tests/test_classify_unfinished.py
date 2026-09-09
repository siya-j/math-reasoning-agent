"""A run that never got to formalising has not failed at formalising.

MEASURED on eval/results/proofnet-60.json, and it cost two goals of nine.

The machine slept mid-run. `budget.elapsed` reads `time.time()`, which counts
sleep -- deliberately, because the budget lives in a workspace file and a
monotonic reading is meaningless across processes -- so the wall-clock
deadline expired while nothing was running. `exercise_1_27` recorded ZERO
model calls, an empty statement, and "stopped early: wall clock spent
(3780s)", and `classify` scored it `not_formalized`. A sleeping laptop went
into the formalisation rate.

The distinction the old code missed: a statement Lean REJECTED is a fact about
the formalisation, whatever happened next. NO statement at all is only a
verdict on formalising if the agent had the chance to declare one.
"""

import pytest

from domain.proof import ProofRun
from eval.proof_metrics import ProofOutcome, classify

CLOCK = "stopped early: wall clock spent (3780s)"
CRASH = "agent failed: ReadError"


# ------------------------------------------------------- THE regression
def test_no_statement_and_the_clock_ran_out_is_exhausted():
    """`exercise_1_27` itself: zero model calls, nothing declared, killed by a
    deadline that had been counting sleep."""
    run = ProofRun(goal="q", statement="", trace=[CLOCK])
    assert classify(run) is ProofOutcome.EXHAUSTED


def test_no_statement_and_a_crash_is_an_error():
    """A crash is evidence about nothing, and that must outrank an empty
    statement for the same reason: the agent never reached formalising."""
    run = ProofRun(goal="q", statement="", trace=[CRASH])
    assert classify(run) is ProofOutcome.ERROR


# ------------------------------------------ what must NOT change
def test_no_statement_after_a_normal_finish_is_still_not_formalized():
    """The case the old behaviour was right about. An agent that ran to a
    normal conclusion and never declared a statement DID fail to formalise."""
    run = ProofRun(goal="q", statement="", trace=[])
    assert classify(run) is ProofOutcome.NOT_FORMALIZED


def test_a_rejected_statement_stays_a_formalisation_failure():
    """The compiler's verdict on the signature is a fact about the
    formalisation, and `classify`'s docstring says so: it holds "whatever
    happened next"."""
    run = ProofRun(goal="q", statement="theorem t : Nonempty (Basis K V)",
                   statement_ok=False, trace=[])
    assert classify(run) is ProofOutcome.NOT_FORMALIZED


def test_a_rejected_statement_stays_that_way_even_if_the_clock_then_ran_out():
    """THE line this fix must not cross. `exercise_5_20` in the same run had a
    statement that genuinely did not elaborate AND ran out of clock. That is a
    real formalisation failure and must not be laundered into `exhausted` --
    otherwise every formalisation failure could escape the rate by also
    running long."""
    run = ProofRun(goal="q", statement="theorem t : End F V",
                   statement_ok=False, trace=[CLOCK])
    assert classify(run) is ProofOutcome.NOT_FORMALIZED


def test_a_proof_still_outranks_everything():
    """Unchanged: a compiler fact beats any of this."""
    from domain.verdict import Verdict, VerificationStatus

    run = ProofRun(goal="q", statement="theorem t : True", proof="by trivial",
                   verdict=Verdict(VerificationStatus.TRUE, "lean", ""),
                   trace=[CLOCK, CRASH])
    assert classify(run) is ProofOutcome.PROVED


# --------------------------------------------- the three stay in step
def test_the_helper_reads_the_same_prefixes_classify_does():
    """`_did_not_finish` matches on trace prefixes that two later branches
    also match on. If they drift, an unfinished run silently becomes a
    formalisation failure again."""
    import inspect

    from eval import proof_metrics

    source = inspect.getsource(proof_metrics.classify)
    helper = inspect.getsource(proof_metrics._did_not_finish)
    for prefix in ('"agent failed"', '"stopped early"'):
        assert prefix in source, f"classify no longer checks {prefix}"
        assert prefix in helper, f"the helper no longer checks {prefix}"


@pytest.mark.parametrize("trace,expected", [
    ([], ProofOutcome.NOT_FORMALIZED),
    ([CLOCK], ProofOutcome.EXHAUSTED),
    ([CRASH], ProofOutcome.ERROR),
    ([CRASH, CLOCK], ProofOutcome.ERROR),
])
def test_the_empty_statement_table(trace, expected):
    """All four endings for a run that declared nothing, in one place."""
    assert classify(ProofRun(goal="q", statement="", trace=trace)) is expected
