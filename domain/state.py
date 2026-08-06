"""Explicit execution state (Principle 5).

Every pipeline step reads from and writes to this one object. Nothing is
hidden in local variables, so any run can be inspected after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from domain.claim import Claim
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
    trace: list[str] = field(default_factory=list)

    def log(self, step: str, detail: str = "") -> None:
        """Record that a pipeline step ran."""
        self.trace.append(f"{step}: {detail}" if detail else step)
