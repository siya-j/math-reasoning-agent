"""Scoring for the proving path.

FOUR NUMBERS, NEVER BLENDED
---------------------------
The miniF2F Revisited paper (arXiv 2511.03108) measured what happens when
autoformalisation and proving are reported as one figure: a 97% formalizer
and a 70.8% prover combined to 34.8% end to end. Two-thirds of the apparent
performance was lost in the join, and nobody could say which half was at
fault.

So this module reports:

    formalisation rate   did a Lean statement come out at all?
    proof rate           of those, how many did the compiler accept?
    lemma yield          how often decomposition rescued a goal
    budget               attempts spent per goal

ERRORS ARE NOT FAILURES
-----------------------
A run that never reached the model says nothing about the model. Errors are
counted and excluded from every rate — the mistake `variance.py` made once
and `probe_lean_model.py` made again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domain.proof import ProofRun, ProofStage
from eval.proof_dataset import Goal, Tier


class ProofOutcome(str, Enum):
    """Four ways to fail, and they are not the same failure.

    Measured on the first ProofNet pilot, where all four collapsed into
    `not_proved` and the number was therefore uninterpretable:

      exercise_1_19b  the statement names `Complex.abs`, which no longer
                      exists in Lean v4.33 -> NOT_FORMALIZED, a benchmark
                      compatibility fact, nothing to do with our prover
      exercise_1_13c  the agent argued the theorem is FALSE as stated, Ω not
                      being assumed connected -> SUSPECT_STATEMENT
      exercise_1_26   elaborated, decomposed correctly, ran out of clock with
                      the sub-lemmas unproved -> EXHAUSTED
      exercise_1_13a  elaborated, had the time, could not find a proof
                      -> NOT_PROVED, the only one that is really about proving

    Reporting one rate over all four would say the prover failed 4/4 when it
    was genuinely tested once.
    """

    PROVED = "proved"                  # the compiler accepted a proof
    NOT_PROVED = "not_proved"          # ran, had the budget, found nothing
    NOT_FORMALIZED = "not_formalized"  # the statement never elaborated
    EXHAUSTED = "exhausted"            # ran out of clock or compilations
    SUSPECT_STATEMENT = "suspect_statement"   # reported false/ill-posed AS STATED
    ERROR = "error"                    # crashed, or never reached the model


@dataclass(frozen=True)
class ProofResult:
    goal_id: str
    area: str
    tier: Tier
    outcome: ProofOutcome
    statement: str = ""
    attempts: int = 0
    lemmas_total: int = 0
    lemmas_proved: int = 0
    via_synthesis: bool = False
    detail: str = ""

    # Cost, so a proof rate is never quoted without the budget that bought it.
    model_calls: int = 0
    lean_calls: int = 0
    retrieval_calls: int = 0
    symbolic_calls: int = 0
    seconds: float = 0.0

    # Without these a failed run is opaque, and a cause has to be guessed at.
    # `trace` says which stages ran and what they decided; `stages` records
    # what each attempt produced and why the compiler refused it.
    trace: tuple[str, ...] = ()
    stages: tuple[dict, ...] = ()

    @property
    def counted(self) -> bool:
        """Did this run actually produce evidence about the system?"""
        return self.outcome is not ProofOutcome.ERROR


def classify(run: ProofRun) -> ProofOutcome:
    """Order matters, and it runs from most to least certain.

    A proof outranks everything: it is a compiler fact. A statement that never
    elaborated cannot have been proved OR disproved by us, so it is next. Only
    then the two ways of not finishing, and they are distinguished by whether
    the agent still had budget when it stopped.
    """
    if run.proved:
        return ProofOutcome.PROVED

    # A statement Lean cannot elaborate is a FORMALISATION failure, not a
    # proving failure. Counting it as "not proved" credited the formalizer
    # with a success it did not have and blamed the prover for a proof that
    # could never have existed.
    if not run.statement.strip() or not run.statement_ok:
        return ProofOutcome.NOT_FORMALIZED

    # Reported by the agent, and labelled as a REPORT rather than a verdict.
    # We cannot confirm a statement is false; we can record that the agent
    # said so and stop counting it against the prover.
    if any("suspect statement" in entry for entry in run.trace):
        return ProofOutcome.SUSPECT_STATEMENT

    # Ran out of clock or compilations. Read from the budget, not from prose.
    if any(entry.startswith("stopped early") for entry in run.trace):
        return ProofOutcome.EXHAUSTED

    return ProofOutcome.NOT_PROVED


def result_from(goal: Goal, run: ProofRun) -> ProofResult:
    return ProofResult(
        goal_id=goal.id,
        area=goal.area,
        tier=goal.tier,
        outcome=classify(run),
        statement=run.statement,
        attempts=len(run.attempts),
        lemmas_total=len(run.lemmas),
        lemmas_proved=len(run.proved_lemmas),
        via_synthesis=bool(
            run.proved
            and run.attempts
            and run.attempts[-1].stage is ProofStage.SYNTHESIS
        ),
        detail=run.verdict.detail if run.verdict else "",
        model_calls=run.telemetry.model_calls,
        lean_calls=run.telemetry.lean_calls,
        retrieval_calls=run.telemetry.retrieval_calls,
        symbolic_calls=run.telemetry.symbolic_calls,
        seconds=round(run.telemetry.seconds, 1),
        trace=tuple(run.trace),
        stages=tuple(
            {
                "stage": attempt.stage.value,
                "proof": attempt.proof[:600],
                "errors": attempt.verdict.detail[:600],
            }
            for attempt in run.attempts
        ),
    )


def _rate(numerator: int, denominator: int) -> float | None:
    """None, not 0.0, when there is nothing to divide by.

    Printing 0% for an empty category reads as a failure rather than an
    absence of data. That bug was already fixed once in eval/metrics.py.
    """
    return round(numerator / denominator, 3) if denominator else None


def summarize(results: list[ProofResult]) -> dict:
    counted = [r for r in results if r.counted]
    errors = len(results) - len(counted)

    formalized = [r for r in counted if r.outcome is not ProofOutcome.NOT_FORMALIZED]
    proved = [r for r in counted if r.outcome is ProofOutcome.PROVED]

    # The prover was only really TESTED where the statement elaborated, the
    # benchmark row was not suspect, and the budget did not run out first.
    # Every other bucket measures something else.
    exhausted = [r for r in counted if r.outcome is ProofOutcome.EXHAUSTED]
    suspect = [r for r in counted if r.outcome is ProofOutcome.SUSPECT_STATEMENT]
    tested = [r for r in formalized if r not in exhausted and r not in suspect]

    # Lemma yield: of the goals that got as far as decomposition, how many
    # were rescued by it? This is the number that justifies Phase 5.
    decomposed = [r for r in counted if r.lemmas_total]
    rescued = [r for r in decomposed if r.via_synthesis]

    summary = {
        "total": len(results),
        "errors": errors,
        "attempted": len(counted),
        "formalization_rate": _rate(len(formalized), len(counted)),
        "proof_rate": _rate(len(proved), len(counted)),
        "proof_rate_of_formalized": _rate(len(proved), len(formalized)),
        "proof_rate_of_tested": _rate(len(proved), len(tested)),
        "not_formalized": len(counted) - len(formalized),
        "exhausted": len(exhausted),
        "suspect_statements": len(suspect),
        "genuinely_tested": len(tested),
        "lemma_yield": _rate(len(rescued), len(decomposed)),
        "mean_attempts": (
            round(sum(r.attempts for r in counted) / len(counted), 2)
            if counted
            else None
        ),
    }

    for tier in Tier:
        in_tier = [r for r in counted if r.tier is tier]
        summary[f"proof_rate_{tier.value}"] = _rate(
            sum(1 for r in in_tier if r.outcome is ProofOutcome.PROVED), len(in_tier)
        )
    return summary


def _percent(value: float | None) -> str:
    return "n/a (no such cases)" if value is None else f"{value:.0%}"


def render(summary: dict) -> str:
    lines = [
        "=" * 52,
        f"  total goals            {summary['total']}",
        f"  attempted              {summary['attempted']}",
        f"  errors (excluded)      {summary['errors']}",
        "-" * 52,
        f"  formalisation rate     {_percent(summary['formalization_rate'])}",
        f"  proof rate             {_percent(summary['proof_rate'])}",
        f"  proof rate | formalised{_percent(summary['proof_rate_of_formalized']):>13}",
        f"  proof rate | tested    {_percent(summary['proof_rate_of_tested'])}",
        "-" * 52,
        f"  statement not elaborable {summary['not_formalized']}",
        f"  statement suspect        {summary['suspect_statements']}",
        f"  budget exhausted         {summary['exhausted']}",
        f"  genuinely tested         {summary['genuinely_tested']}",
        "-" * 52,
        f"  lemma yield            {_percent(summary['lemma_yield'])}",
        f"  mean attempts          {summary['mean_attempts']}",
        "-" * 52,
    ]
    for tier in Tier:
        lines.append(
            f"  {tier.value:<22} {_percent(summary[f'proof_rate_{tier.value}'])}"
        )
    lines.append("=" * 52)
    return "\n".join(lines)
