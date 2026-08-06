"""Explanation (Execution Flow step 5).

Writes the final answer. Its most important job is Success Criterion 4:
clearly communicate WHAT WAS VERIFIED and what was only reasoned about.
"""

from domain.claim import Claim
from domain.verdict import Verdict

PROMPT = """You are the explanation component of a mathematical agent.

Original question: {question}
Interpreted claim: {statement}

Model reasoning (PROBABILISTIC - may be wrong):
{reasoning}

Deterministic verification:
  status: {status}
  method: {method}
  detail: {detail}

Write the final answer for the user. Rules:
- If verification status is 'true' or 'false', that result is AUTHORITATIVE.
  State the answer based on it and say it was verified by {method}.
- If the model reasoning disagrees with verification, trust verification and
  say so plainly.
- If status is 'n/a' or 'unknown', say clearly that this answer comes from
  reasoning only and has NOT been deterministically verified.
Be concise."""


def explain(model, claim: Claim, reasoning: str, verdict: Verdict) -> str:
    """Step 5: produce the final user-facing answer."""
    response = model.invoke(
        PROMPT.format(
            question=claim.original_question,
            statement=claim.statement,
            reasoning=reasoning,
            status=verdict.status.value,
            method=verdict.method,
            detail=verdict.detail,
        )
    )
    return response.text
