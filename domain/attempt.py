"""Execution memory (Design Doc, Principle 3 — Iterative Reasoning).

Every verification attempt is recorded, including the ones that failed.
Failures are learning signals, not terminal errors: the next attempt reads
the previous verdict and tries to do better.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domain.verdict import Verdict
from domain.verification import VerificationRequest


class Strategy(str, Enum):
    """How a given attempt was produced."""

    INITIAL = "initial"            # first pass
    REFORMALIZE = "reformalize"    # same claim, corrected formal check
    REINTERPRET = "reinterpret"    # re-read the question from scratch


@dataclass(frozen=True)
class Attempt:
    """One trip through formalize -> verify, kept for inspection."""

    number: int
    strategy: Strategy
    claim_statement: str
    request: VerificationRequest
    verdict: Verdict

    def summary(self) -> str:
        return (
            f"#{self.number} ({self.strategy.value}): "
            f"{self.request.kind.value} {self.request.lhs} ?= {self.request.rhs} "
            f"-> {self.verdict.status.value}"
        )
