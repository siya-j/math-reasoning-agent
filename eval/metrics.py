"""Scoring (Design Doc section 9 — Evaluation).

Four outcomes, and the distinction between them is the whole point:

    CORRECT  the agent reached the right conclusion
    MISSED   a verdict existed but the agent could not reach it.
             A COVERAGE gap. Disappointing, not dangerous.
    WRONG    the agent asserted a verdict that is not right — including
             claiming verification for something nothing can verify.
             A SOUNDNESS failure. This is the number that must stay at zero.
    ERROR    the run crashed.

MISSED is acceptable and expected. WRONG is not. An agent that says
"unknown" too often is merely limited; one that says "verified" when it
isn't has destroyed the only thing this architecture exists to provide.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domain.verdict import VerificationStatus

DECIDED = (VerificationStatus.TRUE, VerificationStatus.FALSE)


class Outcome(str, Enum):
    CORRECT = "correct"
    MISSED = "missed"
    WRONG = "wrong"
    ERROR = "error"


def classify(expected: VerificationStatus, actual: VerificationStatus) -> Outcome:
    """Compare what should have happened with what did."""
    if actual in DECIDED:
        if expected in DECIDED:
            return Outcome.CORRECT if actual is expected else Outcome.WRONG
        # Claimed a deterministic verdict for something undecidable.
        return Outcome.WRONG
    # Agent did not decide.
    if expected in DECIDED:
        return Outcome.MISSED
    return Outcome.CORRECT


@dataclass
class CaseResult:
    case_id: str
    area: str
    expected: str
    actual: str
    outcome: Outcome
    checks: int = 0            # verifier calls in the deciding attempt
    attempts: int = 0          # agent invocations the pipeline made
    evidence: int = 0          # auxiliary checks gathered when unverified
    tools_used: str = ""       # which tools
    detail: str = ""


def summarize(results: list[CaseResult]) -> dict:
    """Aggregate metrics over a run."""
    total = len(results) or 1
    counts = {outcome: 0 for outcome in Outcome}
    for result in results:
        counts[result.outcome] += 1

    decidable = [r for r in results if r.expected in ("true", "false")]
    # Did the agent even attempt verification? The agentic failure mode is
    # answering from memory without calling a tool at all.
    used_tools = [r for r in results if r.checks > 0]
    abstract = [r for r in results if r.expected == "n/a"]
    restrained = [r for r in abstract if r.checks == 0]

    return {
        "total": len(results),
        "correct": counts[Outcome.CORRECT],
        "missed": counts[Outcome.MISSED],
        "wrong": counts[Outcome.WRONG],
        "errors": counts[Outcome.ERROR],
        "accuracy": counts[Outcome.CORRECT] / total,
        "soundness": 1 - counts[Outcome.WRONG] / total,
        # None, not 0.0, when there is nothing to measure. A slice with no
        # abstract cases scored 0% restraint and read like a failure.
        "coverage": (
            sum(1 for r in decidable if r.outcome is Outcome.CORRECT) / len(decidable)
            if decidable
            else None
        ),
        "tool_use_rate": len(used_tools) / total,
        "mean_checks": sum(r.checks for r in results) / total,
        "mean_attempts": sum(r.attempts for r in results) / total,
        # Did reflection actually rescue anything? Phase 4 earning its keep.
        "recovered_by_retry": sum(
            1 for r in results if r.attempts > 1 and r.outcome is Outcome.CORRECT
        ),
        "restraint_on_abstract": (
            len(restrained) / len(abstract) if abstract else None
        ),
    }
