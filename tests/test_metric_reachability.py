"""Every metric must be capable of a non-default value for every prover.

WHY THIS FILE EXISTS
--------------------
`lemma_yield` read 0.0 for every math_v2 run ever recorded, and the reason was
not that decomposition never worked. `via_synthesis` asked whether
`run.attempts[-1].stage is ProofStage.SYNTHESIS`, and math_v2 CANNOT EMIT that
stage -- `harness._STAGE` maps records to DIRECT and SKELETON only, and
SYNTHESIS is produced solely by the old `pipeline/prover.py`. The gauge read
zero no matter what happened underneath it.

MEASURED, on PutnamBench `putnam_1962_b1`: the agent proved four lemmas and
assembled them into the winning proof, and the run's summary reported a lemma
yield of 0%. A whole strategy was nearly built on that number.

THE CLASS OF BUG, WHICH IS WHAT THIS FILE GUARDS
------------------------------------------------
A metric wired to a value its producer never emits fails SILENTLY and reads as
a real finding. It cannot be caught by a passing test suite, because nothing
is throwing; it cannot be caught by reading the metric's own code, because
that code is correct in isolation. It is only visible where the producer and
the consumer are checked TOGETHER, which is what each test below does.

The soundness layer got adversarial tests and a mutation sweep. This is the
equivalent scrutiny for the capability layer: not "is the number right" but
"can this number ever be anything other than its default".
"""

import pytest

from domain.proof import (
    Lemma,
    ProofAttempt,
    ProofRun,
    ProofStage,
    Telemetry,
    Verdict,
    VerificationStatus,
)
from eval.proof_dataset import Goal, Tier
from eval.proof_metrics import ProofOutcome, ProofResult, classify, result_from, summarize

GOAL = Goal(id="g1", goal="g", area="a", tier=Tier.IN_MATHLIB)
TRUE = Verdict(VerificationStatus.TRUE, "lean", "")
UNKNOWN = Verdict(VerificationStatus.UNKNOWN, "lean", "")


def run_with(**fields) -> ProofRun:
    run = ProofRun(goal="g")
    run.statement = fields.pop("statement", "theorem t : True")
    run.statement_ok = fields.pop("statement_ok", True)
    run.verdict = fields.pop("verdict", UNKNOWN)
    run.telemetry = Telemetry()
    for key, value in fields.items():
        setattr(run, key, value)
    return run


def kept(name: str) -> Lemma:
    return Lemma(informal="", statement=f"lemma {name} : True",
                 proof=f"lemma {name} : True := trivial", verdict=TRUE)


# =====================================================================
# The one that was broken
# =====================================================================
def test_decomposition_is_detectable_when_the_model_assembles_the_proof():
    """THE `putnam_1962_b1` SHAPE, which the old check missed entirely: four
    lemmas proved, then the winning proof cites them by name. math_v2 never
    emits ProofStage.SYNTHESIS, so a stage check could not see this."""
    run = run_with(
        verdict=TRUE,
        proof="by\n  rw [p_eq_prod p h0 hp n z]\n  exact zero_case p h0 hp x y",
        lemmas=[kept("p_eq_prod"), kept("zero_case")],
        attempts=[ProofAttempt(1, ProofStage.DIRECT, "...", TRUE)],
    )

    assert run.proved, "fixture is wrong; nothing below means anything"
    assert result_from(GOAL, run).via_synthesis is True


def test_decomposition_is_still_detectable_via_the_old_prover_stage():
    """The baseline marks its assembled attempt SYNTHESIS. Keeping that path
    is what leaves the two arms comparable."""
    run = run_with(
        verdict=TRUE,
        proof="whatever",
        attempts=[ProofAttempt(1, ProofStage.SYNTHESIS, "...", TRUE)],
    )

    assert result_from(GOAL, run).via_synthesis is True


def test_a_proof_that_ignores_its_lemmas_is_not_decomposition():
    """The negative control. Lemmas existing is not lemmas being USED -- and
    without this the metric would just re-report "did any lemma compile"."""
    run = run_with(
        verdict=TRUE,
        proof="by norm_num",
        lemmas=[kept("unused_helper")],
        attempts=[ProofAttempt(1, ProofStage.DIRECT, "...", TRUE)],
    )

    assert result_from(GOAL, run).via_synthesis is False


def test_lemma_yield_can_be_non_zero_end_to_end():
    """The summary-level statement of the same thing. This is the number that
    read 0.0 on every math_v2 run ever recorded."""
    rescued = result_from(GOAL, run_with(
        verdict=TRUE,
        proof="exact helper_one trivial",
        lemmas=[kept("helper_one")],
        attempts=[ProofAttempt(1, ProofStage.DIRECT, "...", TRUE)],
    ))

    assert summarize([rescued])["lemma_yield"] == 1.0


# =====================================================================
# Every outcome must be reachable from a math_v2-shaped run
# =====================================================================
@pytest.mark.parametrize("outcome,run", [
    (ProofOutcome.PROVED, run_with(verdict=TRUE, proof="by norm_num")),
    (ProofOutcome.NOT_FORMALIZED, run_with(statement_ok=False)),
    (ProofOutcome.REFUTED, run_with(trace=["refuted statement: ..."])),
    (ProofOutcome.SUSPECT_STATEMENT, run_with(trace=["suspect statement: ..."])),
    (ProofOutcome.EXHAUSTED,
     run_with(trace=["stopped early: compilation budget spent (40 compiles)"])),
    (ProofOutcome.NOT_PROVED, run_with()),
])
def test_every_outcome_is_reachable(outcome, run):
    """An outcome nothing can produce is a category that silently never
    appears in any report -- indistinguishable, from the outside, from a
    system that never hits that case."""
    assert classify(run) is outcome


def test_the_exhausted_trace_line_matches_what_the_budget_actually_writes(
        tmp_path):
    """`classify` greps the trace for "stopped early"; `harness._to_proof_run`
    writes that line from `budget.summary()["reason"]`. Two files, one
    literal, no shared constant between them -- so this drives the REAL
    producers and pins the wording. If it ever drifts, every budget-exhausted
    run silently reclassifies as NOT_PROVED and lands in the proof-rate
    denominator, making the agent look worse at proving than it is.
    """
    from math_v2.core import budget

    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.terminate(workdir, "compilation budget spent (40 compiles)")
    reason = budget.summary(workdir)["reason"]
    assert reason, "the budget recorded no reason; the fixture proves nothing"

    # Exactly how harness._to_proof_run renders it.
    written = f"stopped early: {reason}"

    assert classify(run_with(trace=[written])) is ProofOutcome.EXHAUSTED


# =====================================================================
# The cost metrics, added the same day and never yet non-zero in a run
# =====================================================================
def test_token_counts_reach_the_summary():
    record = ProofResult(goal_id="a", area="x", tier=Tier.IN_MATHLIB,
                         outcome=ProofOutcome.PROVED,
                         input_tokens=1000, output_tokens=100)

    summary = summarize([record])

    assert summary["input_tokens"] == 1000
    assert summary["output_tokens"] == 100


def test_lemma_counts_reach_the_record():
    """`lemmas_total`/`lemmas_proved` are what `lemma_yield`'s denominator is
    built from, so a zero here would disable the metric just as effectively as
    the stage bug did."""
    run = run_with(lemmas=[kept("a"), kept("b")])

    record = result_from(GOAL, run)

    assert record.lemmas_total == 2
    assert record.lemmas_proved == 2


# =====================================================================
# Tries at the goal, versus everything that was submitted
# =====================================================================
def test_lemma_and_skeleton_work_is_not_a_try_at_the_goal():
    """MEASURED on `putnam_1962_b1`: 33 recorded attempts, 7 of them skeletons
    and at least 8 of them lemmas, reported as `mean_attempts` 22.6 while
    `harness._STAGE`'s own comment claimed that number counted "tries at the
    goal". Both figures are worth having; conflating them was the bug."""
    run = run_with(attempts=[
        ProofAttempt(1, ProofStage.DIRECT, "...", UNKNOWN),
        ProofAttempt(2, ProofStage.LEMMA, "...", UNKNOWN),
        ProofAttempt(3, ProofStage.SKELETON, "...", UNKNOWN),
        ProofAttempt(4, ProofStage.LEMMA, "...", UNKNOWN),
        ProofAttempt(5, ProofStage.DIRECT, "...", UNKNOWN),
    ])

    record = result_from(GOAL, run)

    assert record.attempts == 5, "the old, broader count must not move"
    assert record.goal_attempts == 2
    assert record.lemma_attempts == 2


def test_an_assembled_proof_counts_as_a_try_at_the_goal():
    """SYNTHESIS is submitted against the goal like any other attempt -- only
    SKELETON and LEMMA are excluded, and for different reasons."""
    run = run_with(attempts=[ProofAttempt(1, ProofStage.SYNTHESIS, "...", TRUE)])

    assert result_from(GOAL, run).goal_attempts == 1


def test_math_v2_can_actually_emit_a_lemma_stage():
    """The producer half. `harness._STAGE` collapsed LEMMA into DIRECT, which
    is what made this measurement impossible -- the same information loss that
    made `via_synthesis` unreadable. Checking the map directly, because that is
    where the loss was."""
    from math_v2.core import log
    from math_v2.harness import _STAGE

    assert _STAGE[log.LEMMA] is ProofStage.LEMMA
    assert _STAGE[log.PROOF] is ProofStage.DIRECT
    assert _STAGE[log.SKELETON] is ProofStage.SKELETON


def test_the_mean_goal_attempts_summary_is_populated():
    record = result_from(GOAL, run_with(attempts=[
        ProofAttempt(1, ProofStage.DIRECT, "...", UNKNOWN),
        ProofAttempt(2, ProofStage.LEMMA, "...", UNKNOWN),
    ]))

    summary = summarize([record])

    assert summary["mean_attempts"] == 2.0
    assert summary["mean_goal_attempts"] == 1.0


def test_lemma_attempts_can_exceed_lemmas_kept():
    """The point of the new field. `lemmas_total` and `lemmas_proved` are equal
    by construction -- both are built from `log.kept_lemmas`, and a lemma is
    only kept once the compiler accepted it -- so "4/4" is the same number
    twice, not a yield. How many tries it took is the part that carries
    information."""
    run = run_with(
        lemmas=[kept("survived")],
        attempts=[ProofAttempt(n, ProofStage.LEMMA, "...", UNKNOWN)
                  for n in range(1, 5)],
    )

    record = result_from(GOAL, run)

    assert record.lemmas_total == record.lemmas_proved == 1
    assert record.lemma_attempts == 4, "three rejected tries are invisible"
