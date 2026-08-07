"""The guard: turns recorded tool results into a verdict the model cannot spin.

Two independent jobs:

1. AGGREGATE — one refutation outweighs any number of confirmations.
2. LINT — a check whose claimed values do not appear in the question is not
   evidence about the question, whatever the verifier said about it.

Both are pure functions of recorded data. No model is consulted.
"""

from __future__ import annotations

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


def banner(verdict: Verdict, checks: list[Check], evidence: list[Check]) -> str:
    """A deterministic honesty header. The model cannot influence this."""
    lines = [f"[{BANNERS[verdict.status]}] via {verdict.method}"]
    for check in checks:
        lines.append(f"  {check.summary()}")
        lines.append(f"      {check.detail_line()}")
    if evidence:
        lines.append("  supporting evidence (NOT proof of the general claim):")
        for check in evidence:
            lines.append(f"    {check.summary()}")
    return "\n".join(lines)
