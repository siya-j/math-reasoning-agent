"""Offline tests for proof scoring.

The scoring rules decide what the numbers mean, so they get tested harder
than the prover does. Two rules carry most of the weight:

  1. errors are excluded from every rate
  2. an empty category reports n/a, never 0%

Both have already caused a wrong conclusion once in this project.
"""

from domain.proof import ProofAttempt, ProofRun, ProofStage
from domain.verdict import Verdict, VerificationStatus as S
from eval.proof_dataset import Goal, Tier, load_goals
from eval.proof_metrics import (
    ProofOutcome,
    ProofResult,
    classify,
    render,
    result_from,
    summarize,
)

ACCEPTED = Verdict(S.TRUE, "lean", "accepted")
REJECTED = Verdict(S.UNKNOWN, "lean", "error: nope")


def goal(tier=Tier.IN_MATHLIB, area="number theory"):
    return Goal(id="g", area=area, goal="a claim", tier=tier)


def run(statement="theorem t : True", proof="", verdict=REJECTED, lemmas=0,
        proved_lemmas=0, synthesis=False):
    from domain.proof import Lemma

    result = ProofRun(goal="a claim")
    result.statement = statement
    result.proof = proof
    result.verdict = verdict
    for index in range(lemmas):
        result.lemmas.append(
            Lemma(
                informal=f"lemma {index}",
                verdict=ACCEPTED if index < proved_lemmas else REJECTED,
            )
        )
    if synthesis:
        result.attempts.append(
            ProofAttempt(1, ProofStage.SYNTHESIS, proof, verdict)
        )
    return result


def outcome(o, tier=Tier.IN_MATHLIB, **kwargs):
    return ProofResult(goal_id="g", area="a", tier=tier, outcome=o, **kwargs)


# ------------------------------------------------------------- classify
def test_an_accepted_proof_is_proved():
    assert classify(run(proof="by trivial", verdict=ACCEPTED)) is ProofOutcome.PROVED


def test_a_rejected_proof_is_not_proved():
    assert classify(run()) is ProofOutcome.NOT_PROVED


def test_no_statement_is_a_formalisation_failure_not_a_proof_failure():
    """Distinguishing these is the whole point of separate metrics."""
    assert classify(run(statement="   ")) is ProofOutcome.NOT_FORMALIZED


# ------------------------------------------------- errors are not failures
def test_errors_are_excluded_from_every_rate():
    """A call that never reached the model says nothing about the model."""
    results = [
        outcome(ProofOutcome.PROVED),
        outcome(ProofOutcome.ERROR),
        outcome(ProofOutcome.ERROR),
    ]
    summary = summarize(results)

    assert summary["errors"] == 2
    assert summary["attempted"] == 1
    assert summary["proof_rate"] == 1.0, "errors dragged the rate down"


def test_all_errors_reports_no_rates_at_all():
    summary = summarize([outcome(ProofOutcome.ERROR)] * 3)
    assert summary["attempted"] == 0
    assert summary["proof_rate"] is None
    assert summary["formalization_rate"] is None


# ------------------------------------------------------- the four numbers
def test_formalisation_and_proving_are_measured_separately():
    """A formalizer that works and a prover that does not must be visible."""
    results = [
        outcome(ProofOutcome.NOT_PROVED),
        outcome(ProofOutcome.NOT_PROVED),
        outcome(ProofOutcome.NOT_FORMALIZED),
    ]
    summary = summarize(results)

    assert summary["formalization_rate"] == round(2 / 3, 3)
    assert summary["proof_rate"] == 0.0
    assert summary["proof_rate_of_formalized"] == 0.0


def test_proof_rate_of_formalized_ignores_unformalised_goals():
    results = [
        outcome(ProofOutcome.PROVED),
        outcome(ProofOutcome.NOT_FORMALIZED),
    ]
    summary = summarize(results)
    assert summary["proof_rate"] == 0.5
    assert summary["proof_rate_of_formalized"] == 1.0


def test_lemma_yield_counts_only_goals_that_reached_decomposition():
    """The number that justifies Phase 5 existing."""
    results = [
        outcome(ProofOutcome.PROVED, lemmas_total=3, lemmas_proved=2, via_synthesis=True),
        outcome(ProofOutcome.NOT_PROVED, lemmas_total=3),
        outcome(ProofOutcome.PROVED),  # never decomposed, must not count
    ]
    assert summarize(results)["lemma_yield"] == 0.5


def test_lemma_yield_is_not_reported_when_nothing_was_decomposed():
    assert summarize([outcome(ProofOutcome.PROVED)])["lemma_yield"] is None


# -------------------------------------------------------------- by tier
def test_rates_are_broken_down_by_tier():
    """Proving 2+2=4 and proving a topology result are not the same result."""
    results = [
        outcome(ProofOutcome.PROVED, tier=Tier.IN_MATHLIB),
        outcome(ProofOutcome.NOT_PROVED, tier=Tier.NEAR_MATHLIB),
    ]
    summary = summarize(results)
    assert summary["proof_rate_in-mathlib"] == 1.0
    assert summary["proof_rate_near-mathlib"] == 0.0
    assert summary["proof_rate_novel"] is None


def test_an_empty_tier_renders_as_not_applicable_not_zero():
    """0% reads as failure; the truth is that there were no such cases."""
    text = render(summarize([outcome(ProofOutcome.PROVED)]))
    assert "n/a (no such cases)" in text


# ------------------------------------------------------------- building
def test_a_result_is_built_from_a_proof_run():
    result = result_from(
        goal(), run(proof="by trivial", verdict=ACCEPTED, lemmas=2, proved_lemmas=1)
    )
    assert result.outcome is ProofOutcome.PROVED
    assert result.lemmas_total == 2
    assert result.lemmas_proved == 1


def test_the_trace_and_every_attempt_are_recorded():
    """A failed run whose cause has to be guessed at is not a measurement.

    The summary says `not proved` after six attempts; it does not say what
    those attempts contained or why the compiler refused them.
    """
    attempted = run(verdict=REJECTED)
    attempted.log("skeleton", "rejected: the decomposition does not typecheck")
    attempted.attempts.append(
        ProofAttempt(1, ProofStage.DIRECT, "by nonsense", REJECTED)
    )

    result = result_from(goal(), attempted)

    assert result.stages, "no attempt sources recorded"
    assert result.stages[0]["proof"] == "by nonsense"
    assert "error" in result.stages[0]["errors"]
    assert any("does not typecheck" in entry for entry in result.trace)


def test_recorded_sources_are_bounded():
    """A proof is small; a Mathlib error dump is not."""
    attempted = run(verdict=REJECTED)
    attempted.attempts.append(
        ProofAttempt(1, ProofStage.DIRECT, "x" * 5000, REJECTED)
    )
    assert len(result_from(goal(), attempted).stages[0]["proof"]) <= 600


def test_synthesis_is_recorded_so_lemma_yield_can_be_computed():
    result = result_from(
        goal(),
        run(proof="by synth", verdict=ACCEPTED, lemmas=1, proved_lemmas=1,
            synthesis=True),
    )
    assert result.via_synthesis


# -------------------------------------------------------------- dataset
def test_the_goal_set_loads_and_is_well_formed():
    goals = load_goals()
    assert len(goals) >= 12
    assert len({g.id for g in goals}) == len(goals), "duplicate ids"
    assert all(g.goal.strip() for g in goals)


def test_every_curated_tier_is_represented():
    """PROOFNET is excluded on purpose: it is an EXTERNAL benchmark.

    It lives in its own file, loaded with --goals, so that a run against 365
    problems we did not choose can never perturb the fifteen we did.
    """
    curated = set(Tier) - {Tier.PROOFNET}
    tiers = {g.tier for g in load_goals()}

    assert tiers == curated, f"missing tiers: {curated - tiers}"
    assert Tier.PROOFNET not in tiers, "an external benchmark leaked into eval/proofs.json"


def test_in_mathlib_goals_name_the_theorem_where_known():
    """So a failure can be attributed to the model rather than the library."""
    in_mathlib = [g for g in load_goals() if g.tier is Tier.IN_MATHLIB]
    assert in_mathlib
    assert any(g.mathlib for g in in_mathlib)
