"""Retry policy (Design Doc, Principle 3).

This module holds ONE decision: may we try again?

Getting this wrong would be worse than having no retries at all. If a FALSE
verdict triggered a retry, the formalizer would keep rewriting the check
until SymPy finally agreed — an agreement machine, not a verifier. So:

    TRUE            -> stop. Verified.
    FALSE           -> stop. The claim is false; that IS the answer.
    NOT_APPLICABLE  -> stop. No verifier exists; retrying cannot conjure one.
    UNKNOWN         -> retry. The verifier tried and could not decide, which
                       usually means the formal check was malformed.
"""

from __future__ import annotations

from domain.attempt import Strategy
from domain.verdict import Verdict, VerificationStatus


def should_retry(verdict: Verdict) -> bool:
    """Only an undecidable verdict justifies another attempt."""
    return verdict.status is VerificationStatus.UNKNOWN


def next_strategy(attempt_number: int) -> Strategy:
    """Escalate: fix the formal check first, then re-read the question.

    Attempt 2 assumes the claim was understood but written badly.
    Attempt 3+ assumes the claim itself was misread.
    """
    return Strategy.REFORMALIZE if attempt_number == 2 else Strategy.REINTERPRET
