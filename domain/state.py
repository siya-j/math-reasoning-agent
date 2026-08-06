"""Explicit execution state (Principle 5).

Every pipeline step reads from and writes to this one object. Nothing is
hidden in local variables, so any run can be inspected after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from domain.attempt import Attempt
from domain.claim import Claim
from domain.subclaim import SubClaim
from domain.verdict import Verdict
from domain.verification import VerificationRequest


@dataclass
class ReasoningState:
    """Carries everything produced during one run of the pipeline."""

    question: str
    claim: Optional[Claim] = None
    request: Optional[VerificationRequest] = None
    reasoning: Optional[str] = None
    verdict: Optional[Verdict] = None
    explanation: Optional[str] = None

    # Phase 4: every attempt, including the failed ones.
    attempts: list[Attempt] = field(default_factory=list)

    # Phase 5: auxiliary claims checked as evidence. NEVER changes `verdict`.
    subclaims: list[SubClaim] = field(default_factory=list)

    trace: list[str] = field(default_factory=list)

    def log(self, step: str, detail: str = "") -> None:
        """Record that a pipeline step ran."""
        self.trace.append(f"{step}: {detail}" if detail else step)

    def record(self, attempt: Attempt) -> None:
        """Store a verification attempt and make its results current."""
        self.attempts.append(attempt)
        self.request = attempt.request
        self.verdict = attempt.verdict
        self.log("attempt", attempt.summary())
