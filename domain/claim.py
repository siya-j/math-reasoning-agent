"""What a mathematical claim IS. No framework code lives here (Principle 6)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProblemType(str, Enum):
    """How we classify a problem. Decides which verifier (if any) can help."""

    PRIMALITY = "primality"
    ARITHMETIC = "arithmetic"
    ALGEBRA = "algebra"
    CALCULUS = "calculus"
    PROOF = "proof"
    OTHER = "other"


@dataclass(frozen=True)
class Claim:
    """A user's question, restated as a precise mathematical claim.

    original_question: what the user actually typed.
    statement:         the claim in one precise sentence.
    problem_type:      classification, used for verifier routing.
    numbers:           integers found in the question, so verifiers do not
                       have to re-parse natural language.
    """

    original_question: str
    statement: str
    problem_type: ProblemType
    numbers: tuple[int, ...] = ()
