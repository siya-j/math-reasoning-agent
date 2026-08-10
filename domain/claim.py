"""The interpreted claim (Design Doc §9 — Domain, and §10 steps 1-2).

The user asks a question in English. Before anything can verify or prove it,
two things must be decided: what exactly is being claimed, and what kind of
system could settle it. This type carries both.

No framework code, no Lean, no SymPy. A Claim describes the question, not
the machinery that will answer it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProblemType(str, Enum):
    """Which engine could decide this claim.

    The classification is a routing hint, never a verdict. Getting it wrong
    costs an extra attempt, not a wrong answer — the guard still decides.
    """

    COMPUTATIONAL = "computational"  # a CAS can compute it: derivatives, primes
    FORMAL = "formal"                # needs a proof: topology, group theory
    UNSUPPORTED = "unsupported"      # neither can settle it


@dataclass(frozen=True)
class Claim:
    question: str                                  # exactly what was asked
    statement: str = ""                            # a normalised restatement
    problem_type: ProblemType = ProblemType.COMPUTATIONAL
    reason: str = ""                               # why that classification

    @property
    def text(self) -> str:
        """What downstream stages should work from."""
        return self.statement.strip() or self.question
