"""Decomposition (Design Doc, Principle 4 — inspired by Prover Agent).

When the main claim cannot be verified, ask the model for auxiliary claims
that CAN be: special cases, concrete instances, or simpler derived facts.

The model proposes; the verifier decides. As everywhere else in this system,
the model is not permitted to conclude anything.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from domain.claim import Claim
from domain.verification import VerificationKind, VerificationRequest


class _AuxiliaryClaim(BaseModel):
    """One checkable auxiliary fact."""

    description: str = Field(
        description="Plain English, e.g. 'the case n = 3' or 'the derivative at x = 0'."
    )
    kind: Literal["equality", "numeric", "primality", "solution", "none"] = Field(
        description="Which check this is, same meanings as the main formalizer."
    )
    lhs: str = Field(default="", description="Left side in SymPy syntax.")
    rhs: str = Field(default="", description="Right side in SymPy syntax.")
    variable: str = Field(default="x", description="The main variable.")
    candidate: str = Field(default="", description="Claimed solutions, if kind=solution.")


class _Decomposition(BaseModel):
    """A set of auxiliary claims supporting a harder claim."""

    subclaims: list[_AuxiliaryClaim] = Field(
        default_factory=list,
        description="Between 0 and 5 auxiliary claims. Empty if none are checkable.",
    )


PROMPT = """You are the decomposition component of a mathematical agent.

A claim could not be verified directly:

  Claim: {statement}
  Why it failed: {failure}

Propose up to {limit} AUXILIARY claims that a computer algebra system CAN
check, and that would be evidence about the original claim. Useful kinds:

  - concrete special cases (substitute n = 1, 2, 3, ... into a general formula)
  - simpler derived facts implied by the claim
  - numeric instances of a symbolic statement

Rules:
  - Every auxiliary claim must be TRUE if the original claim is true.
  - Use SymPy syntax: ** for powers, explicit multiplication (2*x, not 2x).
  - Do NOT restate the original claim. Do NOT propose anything unverifiable.
  - If no checkable auxiliary claim exists (for example a claim about
    arbitrary vector spaces or topological spaces), return an empty list."""


def decompose(model, claim: Claim, failure: str, limit: int = 4):
    """Return a list of (description, VerificationRequest) pairs."""
    structured_model = model.with_structured_output(_Decomposition)
    parsed = structured_model.invoke(
        PROMPT.format(statement=claim.statement, failure=failure, limit=limit)
    )

    results = []
    for item in parsed.subclaims[:limit]:
        if item.kind == "none":
            continue
        results.append(
            (
                item.description.strip(),
                VerificationRequest(
                    kind=VerificationKind(item.kind),
                    lhs=item.lhs.strip(),
                    rhs=item.rhs.strip(),
                    variable=item.variable.strip() or "x",
                    candidate=item.candidate.strip(),
                ),
            )
        )
    return results
