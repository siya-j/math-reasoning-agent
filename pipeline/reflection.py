"""Retry policy (Design Doc, Principle 3 — Iterative Reasoning).

The PIPELINE decides whether to try again, not the model. That is the whole
point: when this lived inside the agent loop, a small model simply chose not
to iterate, and Phase 4 existed on paper only.

    TRUE / FALSE    -> stop. The question is answered.
    UNKNOWN         -> retry. The check was probably malformed.
    NOT_APPLICABLE  -> nudge ONCE. Either the agent forgot to verify, or the
                       claim genuinely cannot be checked. One retry
                       distinguishes them; more would be pressure to fabricate.

Never retry on FALSE. Retrying until the verifier agrees would turn a
verifier into an agreement machine.
"""

from __future__ import annotations

from domain.attempt import Attempt, Strategy
from domain.verdict import Verdict, VerificationStatus

MALFORMED_FEEDBACK = """Your previous verification attempt could not be decided.

The verifier reported:
  {detail}

Common causes: using ^ instead of **, implicit multiplication (2x instead of
2*x), an undefined function name, or an expression that is not well posed.
Rewrite the check and try again. If this claim genuinely cannot be checked
by a computer algebra system, say so and call no tools."""

NO_TOOLS_FEEDBACK = """You answered without calling any verification tool, so
nothing was verified.

If any part of this question can be checked deterministically, check it now.
If it genuinely cannot — a claim about arbitrary vector spaces, topological
spaces, or a general proof — say so plainly and call no tools. Do not invent
an unrelated check in order to appear rigorous."""


def next_strategy(verdict: Verdict, attempts: list[Attempt]) -> Strategy | None:
    """Which retry to make next, or None to stop."""
    if verdict.status in (VerificationStatus.TRUE, VerificationStatus.FALSE):
        return None

    if verdict.status is VerificationStatus.UNKNOWN:
        return Strategy.RETRY_MALFORMED

    # NOT_APPLICABLE: one nudge only.
    already_nudged = any(a.strategy is Strategy.RETRY_NO_TOOLS for a in attempts)
    return None if already_nudged else Strategy.RETRY_NO_TOOLS


def feedback_for(strategy: Strategy, verdict: Verdict) -> str:
    if strategy is Strategy.RETRY_MALFORMED:
        return MALFORMED_FEEDBACK.format(detail=verdict.detail)
    return NO_TOOLS_FEEDBACK
