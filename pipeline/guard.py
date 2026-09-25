"""The guard: turns recorded tool results into a verdict the model cannot spin.

Two independent jobs:

1. AGGREGATE — one refutation outweighs any number of confirmations.
2. LINT — a check whose claimed values do not appear in the question is not
   evidence about the question, whatever the verifier said about it.

Both are pure functions of recorded data. No model is consulted.
"""

from __future__ import annotations

import verifiers
from domain.check import Check
from domain.verdict import Verdict, VerificationStatus
from pipeline.faithfulness import unsupported_numbers

BANNERS = {
    VerificationStatus.TRUE: "VERIFIED TRUE",
    VerificationStatus.FALSE: "VERIFIED FALSE",
    VerificationStatus.UNKNOWN: "NOT VERIFIED (checks were inconclusive)",
    VerificationStatus.NOT_APPLICABLE: "NOT VERIFIED (reasoning only)",
}


def unfaithful_checks(question: str, checks: list[Check]) -> list[tuple[Check, list[str]]]:
    """Checks containing values the question never mentioned."""
    flagged = []
    for check in checks:
        extra = unsupported_numbers(question, check.request)
        if extra:
            flagged.append((check, extra))
    return flagged


def decide(question: str, checks: list[Check]) -> Verdict:
    """Compute the verdict from recorded checks alone."""
    if not checks:
        return Verdict(
            status=VerificationStatus.NOT_APPLICABLE,
            method="none",
            detail="The agent performed no deterministic verification.",
        )

    # A check that confirms values the user never claimed is not a
    # confirmation of the user's claim. Refuse rather than endorse it.
    flagged = unfaithful_checks(question, checks)
    if flagged:
        check, extra = flagged[0]
        return Verdict(
            status=VerificationStatus.UNKNOWN,
            method="faithfulness lint",
            detail=(
                f"The check used {', '.join(extra)}, which the question never "
                f"mentions, so it tested a different claim than the one asked. "
                f"Recorded claim: {check.claim!r}"
            ),
        )

    # ABSTENTIONS ARE NOT INCONCLUSIVE RESULTS.
    #
    # A refutation-only check says "that is impossible" or it says nothing.
    # Its UNKNOWN means "I found nothing wrong", so counting it among the
    # checks that failed to decide would downgrade a correctly verified
    # answer every time the agent sanity-checked its own work — punishing
    # precisely the behaviour the sanity check exists to encourage.
    #
    # A FALSE from such a check still counts, and counts decisively. Only
    # its UNKNOWN is set aside.
    decisive = [
        check for check in checks
        if not (
            check.verdict.status is VerificationStatus.UNKNOWN
            and check.verdict.method in verifiers.REFUTATION_ONLY
        )
    ]

    if not decisive:
        return Verdict(
            status=VerificationStatus.NOT_APPLICABLE,
            method="none",
            detail=(
                f"The agent made {len(checks)} check(s), all of which can "
                "only refute and none of which refuted anything. Nothing was "
                "confirmed, because no check here was capable of confirming."
            ),
        )

    checks = decisive
    statuses = [c.verdict.status for c in checks]

    if VerificationStatus.FALSE in statuses:
        refuted = next(c for c in checks if c.verdict.status is VerificationStatus.FALSE)
        return Verdict(
            status=VerificationStatus.FALSE,
            method=refuted.verdict.method,
            detail=f"Refuted by {refuted.tool}: {refuted.verdict.detail}",
        )

    if all(status is VerificationStatus.TRUE for status in statuses):
        return Verdict(
            status=VerificationStatus.TRUE,
            method=checks[0].verdict.method,
            detail=f"All {len(checks)} check(s) passed. {checks[0].verdict.detail}",
        )

    passed = sum(1 for s in statuses if s is VerificationStatus.TRUE)
    return Verdict(
        status=VerificationStatus.UNKNOWN,
        method="sympy",
        detail=(
            f"{passed} of {len(checks)} check(s) passed; "
            "the rest could not be decided."
        ),
    )


def banner(verdict: Verdict, checks: list[Check], evidence: list[Check],
           computations=()) -> str:
    """A deterministic honesty header. The model cannot influence this.

    COMPUTED IS NOT VERIFIED, and this is where that distinction has to
    survive contact with a reader. A checked claim was tested against a
    value the USER supplied, and that value independently cross-checks the
    model's formalisation -- a wrong formula disagrees with it. A computed
    value has no such check: the engine produced the number, but the model
    chose the formula and nothing tested that choice.

    So the formalisation is printed beside every computed answer, because
    the reader is the only check it has.
    """
    lines = []

    for done in computations or ():
        lines.append(f"[COMPUTED via {done.method}] {done.summary()}")
        lines.append(done.report())

    lines.append(f"[{BANNERS[verdict.status]}] via {verdict.method}")

    # Said plainly, because "performed no deterministic verification"
    # printed beside a computed number reads as a contradiction rather than
    # as the distinction it is.
    if computations and not checks:
        lines.append(
            "  Nothing was CHECKED: the question stated no value to test. "
            "The figure above was computed by a verifier, but the formula "
            "was the model's choice and nothing independent confirms it. "
            "Read the formula."
        )

    for check in checks:
        lines.append(f"  {check.summary()}")
        lines.append(f"      {check.detail_line()}")
    if evidence:
        lines.append("  supporting evidence (NOT proof of the general claim):")
        for check in evidence:
            lines.append(f"    {check.summary()}")
    return "\n".join(lines)
