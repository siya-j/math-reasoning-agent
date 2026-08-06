"""Orchestration (Design Doc section 10).

The ONLY module that knows the complete workflow:

    User Input -> Claim Interpretation -> Problem Classification
               -> Formalization -> Reasoning
               -> Deterministic Verification (when applicable)
               -> Explanation -> Final Response

Formalization is new in Phase 3: it extends the flow rather than replacing
it, exactly as the design document intends.
"""

from __future__ import annotations

import verifiers
from domain.state import ReasoningState
from llm import explain, formalize, get_model, interpret, reason


def run(question: str) -> ReasoningState:
    """Run the full pipeline once and return the completed state."""
    state = ReasoningState(question=question)
    model = get_model()

    # Steps 1-2: interpretation and classification.
    state.claim = interpret(model, question)
    state.log("interpret", state.claim.statement)
    state.log("classify", state.claim.problem_type.value)

    # Step 3: formalization — turn the claim into a checkable request.
    state.request = formalize(model, state.claim)
    state.log("formalize", f"{state.request.kind.value}: {state.request.lhs} ?= {state.request.rhs}")

    # Step 4: probabilistic reasoning.
    state.reasoning = reason(model, state.claim)
    state.log("reason", f"{len(state.reasoning)} chars")

    # Step 5: deterministic verification. Pure Python, no model involved.
    state.verdict = verifiers.verify(state.request)
    state.log("verify", f"{state.verdict.status.value} via {state.verdict.method}")

    # Step 6: explanation that separates verified from unverified.
    state.explanation = explain(model, state.claim, state.reasoning, state.verdict)
    state.log("explain", "done")

    return state
