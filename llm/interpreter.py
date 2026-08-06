"""Claim interpretation + problem classification (Execution Flow steps 1-2).

Turns messy natural language into a precise domain.Claim.

LangChain docs used: model.with_structured_output(schema) — the model is
forced to answer in a fixed shape instead of free prose.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from domain.claim import Claim, ProblemType

# This Pydantic schema is a LangChain/LLM concern, so it lives HERE and not
# in domain/. We convert it into a plain domain.Claim before returning.
class _InterpretedClaim(BaseModel):
    """A mathematical claim extracted from a user's question."""

    statement: str = Field(
        description="The user's question restated as one precise mathematical claim."
    )
    problem_type: Literal[
        "primality", "arithmetic", "algebra", "calculus", "proof", "other"
    ] = Field(description="Which area of mathematics this claim belongs to.")
    numbers: list[int] = Field(
        default_factory=list,
        description="Every integer mentioned in the question, in order of appearance.",
    )


PROMPT = """You are the claim interpretation component of a mathematical agent.

Read the user's question and restate it as one precise mathematical claim.
Do NOT solve it. Do NOT explain. Only interpret and classify.

User question: {question}"""


REINTERPRET_PROMPT = """You are the claim interpretation component of a mathematical agent.

Your previous interpretation of this question could not be verified, even
after the formal check was corrected. The claim itself may have been read
wrongly.

Question: {question}
Previous interpretation: {previous}
What went wrong: {failure}

Restate the claim differently — more precisely, or capturing a different
reading of the question. Do NOT solve it."""


def _to_claim(parsed: _InterpretedClaim, question: str) -> Claim:
    """Boundary: LLM-shaped object -> our framework-free type."""
    return Claim(
        original_question=question,
        statement=parsed.statement,
        problem_type=ProblemType(parsed.problem_type),
        numbers=tuple(parsed.numbers),
    )


def interpret(model, question: str) -> Claim:
    """Step 1+2: interpret the question and classify it."""
    structured_model = model.with_structured_output(_InterpretedClaim)
    parsed = structured_model.invoke(PROMPT.format(question=question))
    return _to_claim(parsed, question)


def reinterpret(model, previous: Claim, failure: str) -> Claim:
    """Phase 4: re-read the question after formalization kept failing."""
    structured_model = model.with_structured_output(_InterpretedClaim)
    parsed = structured_model.invoke(
        REINTERPRET_PROMPT.format(
            question=previous.original_question,
            previous=previous.statement,
            failure=failure,
        )
    )
    return _to_claim(parsed, previous.original_question)
