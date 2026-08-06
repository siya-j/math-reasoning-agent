"""Auxiliary sub-claims (Design Doc, Principle 4 — Hierarchical Problem Solving).

Following Prover Agent: auxiliary lemmas are not only subgoals. They can be
special cases or useful derived facts. We use them the same way — as
EVIDENCE about a claim we could not decide directly.

IMPORTANT: evidence is not proof. Verifying that a formula holds for n = 1..10
says nothing conclusive about all n. SubClaim results therefore never change
the main verdict; they are reported alongside it.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationRequest


@dataclass(frozen=True)
class SubClaim:
    """One auxiliary fact, and what the verifier said about it."""

    description: str          # plain English, e.g. "the case n = 3"
    request: VerificationRequest
    verdict: Verdict

    @property
    def supports(self) -> bool:
        return self.verdict.status is VerificationStatus.TRUE

    @property
    def refutes(self) -> bool:
        return self.verdict.status is VerificationStatus.FALSE

    def summary(self) -> str:
        return f"[{self.verdict.status.value}] {self.description}"
