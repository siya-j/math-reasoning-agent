"""Formalization: natural-language claim -> machine-checkable request.

This is the single most important step in the whole architecture. It is the
point where a probabilistic system hands work to a deterministic one.

The LLM does NOT decide whether the claim is true. It only decides what
question to ask SymPy. If it formalizes badly, the verifier will say so.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from domain.claim import Claim
from domain.verification import VerificationKind, VerificationRequest


class _FormalizedCheck(BaseModel):
    """A mathematical claim rewritten in SymPy syntax."""

    kind: Literal["equality", "numeric", "primality", "solution", "none"] = Field(
        description=(
            "equality: the claim says two expressions are equal for all values "
            "(derivatives, integrals, identities). "
            "numeric: the claim says an expression evaluates to a number. "
            "primality: the claim is about whether an integer is prime. "
            "solution: the claim states the solutions of an equation. "
            "none: the claim cannot be checked by a computer algebra system."
        )
    )
    lhs: str = Field(
        default="",
        description=(
            "Left side, in SymPy syntax. Use ** for powers, diff(expr, x) for "
            "derivatives, integrate(expr, x) for integrals. "
            "For primality: just the integer. "
            "For solution: the left side of the equation."
        ),
    )
    rhs: str = Field(
        default="",
        description="Right side, in SymPy syntax. Empty if not applicable.",
    )
    variable: str = Field(
        default="x", description="The main variable, e.g. 'x'."
    )
    candidate: str = Field(
        default="",
        description=(
            "For kind='solution' only: the claimed solutions, comma separated, "
            "e.g. '2, -2'. Empty if the claim does not state them."
        ),
    )


PROMPT = """You are the formalization component of a mathematical agent.

Rewrite the claim below as a check that SymPy can perform.
Do NOT solve it. Do NOT decide whether it is true. Only translate.

Examples:
  "the derivative of x^3 is 3x^2"
      -> kind=equality, lhs="diff(x**3, x)", rhs="3*x**2"
  "sin^2(x) + cos^2(x) = 1"
      -> kind=equality, lhs="sin(x)**2 + cos(x)**2", rhs="1"
  "7919 is prime"
      -> kind=primality, lhs="7919"
  "2 + 2 = 5"
      -> kind=numeric, lhs="2 + 2", rhs="5"
  "the solutions of x^2 = 4 are 2 and -2"
      -> kind=solution, lhs="x**2", rhs="4", variable="x", candidate="2, -2"
  "every vector space has a basis"
      -> kind=none

Claim: {statement}
Original question: {question}"""


def formalize(model, claim: Claim) -> VerificationRequest:
    """Translate a claim into a VerificationRequest."""
    structured_model = model.with_structured_output(_FormalizedCheck)
    parsed = structured_model.invoke(
        PROMPT.format(statement=claim.statement, question=claim.original_question)
    )

    # Boundary: convert the LLM-shaped object into our framework-free type.
    return VerificationRequest(
        kind=VerificationKind(parsed.kind),
        lhs=parsed.lhs.strip(),
        rhs=parsed.rhs.strip(),
        variable=parsed.variable.strip() or "x",
        candidate=parsed.candidate.strip(),
    )
