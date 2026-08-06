"""Orchestration (Design Doc section 10).

The ONLY module that knows the complete workflow:

    User Input -> Claim Interpretation -> Problem Classification
               -> Formalization -> Verification  <-- Phase 4 loops here
               -> Reasoning -> Explanation -> Final Response

Phase 4 wraps formalization and verification in a retry loop (Principle 3):

    Attempt -> Verification -> Feedback -> Improved Attempt

Reasoning moved to AFTER the loop. The loop can change the claim, and
reasoning about a claim we then discard would be wasted work.
"""

from __future__ import annotations

import config
import verifiers
from domain.attempt import Attempt, Strategy
from domain.state import ReasoningState
from llm import (
    explain,
    formalize,
    get_model,
    interpret,
    reason,
    reformalize,
    reinterpret,
)
from pipeline.reflection import next_strategy, should_retry


def _verify_with_reflection(model, state: ReasoningState) -> None:
    """Formalize and verify, retrying while the verifier cannot decide."""
    claim = interpret(model, state.question)
    state.log("interpret", claim.statement)
    state.log("classify", claim.problem_type.value)

    request = formalize(model, claim)
    verdict = verifiers.verify(request)
    state.claim = claim
    state.record(Attempt(1, Strategy.INITIAL, claim.statement, request, verdict))

    number = 1
    while should_retry(verdict) and number < config.MAX_ATTEMPTS:
        number += 1
        strategy = next_strategy(number)
        state.log("reflect", f"attempt {number} via {strategy.value}")

        if strategy is Strategy.REFORMALIZE:
            # Same claim, corrected formal check.
            request = reformalize(model, claim, request, verdict.detail)
        else:
            # The claim itself may have been misread — start from the question.
            claim = reinterpret(model, claim, verdict.detail)
            state.claim = claim
            request = formalize(model, claim)

        verdict = verifiers.verify(request)
        state.record(Attempt(number, strategy, claim.statement, request, verdict))


def run(question: str, model=None) -> ReasoningState:
    """Run the full pipeline once and return the completed state.

    `model` can be injected for testing, so the loop is testable offline.
    """
    state = ReasoningState(question=question)
    model = model or get_model()

    # Steps 1-4, with reflection.
    _verify_with_reflection(model, state)

    # Step 5: probabilistic reasoning about the final claim.
    state.reasoning = reason(model, state.claim)
    state.log("reason", f"{len(state.reasoning)} chars")

    # Step 6: explanation that separates verified from unverified.
    state.explanation = explain(model, state.claim, state.reasoning, state.verdict)
    state.log("explain", f"after {len(state.attempts)} attempt(s)")

    return state
