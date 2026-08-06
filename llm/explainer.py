"""Explanation (final step of the execution flow).

Writes the final answer. Its most important job is Success Criterion 4:
clearly communicate WHAT WAS VERIFIED and what was only reasoned about.
"""

from __future__ import annotations

from domain.claim import Claim
from domain.subclaim import SubClaim
from domain.verdict import Verdict

PROMPT = """You are the explanation component of a mathematical agent.

Original question: {question}
Interpreted claim: {statement}

Model reasoning (PROBABILISTIC - may be wrong):
{reasoning}

Deterministic verification of the MAIN claim:
  status: {status}
  method: {method}
  detail: {detail}

Auxiliary claims that were checked (evidence only):
{evidence}

Write the final answer for the user. Rules:
- If the main status is 'true' or 'false', that result is AUTHORITATIVE.
  State the answer based on it and say it was verified by {method}.
- If the model reasoning disagrees with verification, trust verification and
  say so plainly.
- If the main status is 'n/a' or 'unknown', say clearly that the answer comes
  from reasoning only and has NOT been deterministically verified.
- Auxiliary claims are EVIDENCE, NOT PROOF. Verified special cases do not
  establish a general statement. Report them as supporting evidence and never
  describe the main claim as proven because of them.
- If any auxiliary claim was refuted (status false), highlight it: it is a
  counterexample and the main claim is probably false.
Be concise."""

NO_EVIDENCE = "  (none)"


def _format_evidence(subclaims: list[SubClaim] | None) -> str:
    if not subclaims:
        return NO_EVIDENCE
    return "\n".join(f"  {s.summary()} - {s.verdict.detail}" for s in subclaims)


def explain(
    model,
    claim: Claim,
    reasoning: str,
    verdict: Verdict,
    subclaims: list[SubClaim] | None = None,
) -> str:
    """Produce the final user-facing answer."""
    response = model.invoke(
        PROMPT.format(
            question=claim.original_question,
            statement=claim.statement,
            reasoning=reasoning,
            status=verdict.status.value,
            method=verdict.method,
            detail=verdict.detail,
            evidence=_format_evidence(subclaims),
        )
    )
    return response.text
