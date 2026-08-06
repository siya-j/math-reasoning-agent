"""Reasoning (Execution Flow step 3).

Produces the mathematical argument. This output is PROBABILISTIC — it is
the model's reasoning, not a verified fact. The pipeline treats it that way.
"""

from domain.claim import Claim

PROMPT = """You are the reasoning component of a mathematical agent.

Claim: {statement}
Type: {problem_type}

Reason about this claim step by step. Be concise and precise.
If you are uncertain about any step, say so explicitly rather than guessing."""


def reason(model, claim: Claim) -> str:
    """Step 3: reason about the claim. Returns plain text."""
    response = model.invoke(
        PROMPT.format(
            statement=claim.statement,
            problem_type=claim.problem_type.value,
        )
    )
    return response.text
