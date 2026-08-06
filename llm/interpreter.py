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


def interpret(model, question: str) -> Claim:
    """Step 1+2: interpret the question and classify it."""
    structured_model = model.with_structured_output(_InterpretedClaim)
    parsed = structured_model.invoke(PROMPT.format(question=question))

    # Boundary: convert the LLM-shaped object into our framework-free type.
    return Claim(
        original_question=question,
        statement=parsed.statement,
        problem_type=ProblemType(parsed.problem_type),
        numbers=tuple(parsed.numbers),
    )
