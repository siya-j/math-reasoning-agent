"""Orchestration (Design Doc section 10).

The ONLY module that knows the complete workflow:

    User Input -> Claim Interpretation -> Problem Classification
               -> Formalization -> Verification      <-- Phase 4 loops here
               -> Decomposition (only if unverified)  <-- Phase 5
               -> Reasoning -> Explanation -> Final Response

Phase 4 (Principle 3): retry while the verifier cannot decide.
Phase 5 (Principle 4): if it still cannot, gather auxiliary evidence.

Evidence never overrides the verdict. See domain/subclaim.py.
"""

from __future__ import annotations

import config
import verifiers
from domain.attempt import Attempt, Strategy
from domain.state import ReasoningState
from domain.subclaim import SubClaim
from llm import (
    decompose,
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


def _gather_evidence(model, state: ReasoningState) -> None:
    """Check auxiliary claims when the main claim could not be decided."""
    proposals = decompose(
        model, state.claim, state.verdict.detail, limit=config.MAX_SUBCLAIMS
    )
    if not proposals:
        state.log("decompose", "no checkable auxiliary claims")
        return

    for description, request in proposals:
        verdict = verifiers.verify(request)
        state.subclaims.append(SubClaim(description, request, verdict))

    supported = sum(1 for s in state.subclaims if s.supports)
    refuted = sum(1 for s in state.subclaims if s.refutes)
    state.log(
        "decompose",
        f"{len(state.subclaims)} auxiliary claims: {supported} true, {refuted} false",
    )


def run(question: str, model=None) -> ReasoningState:
    """Run the full pipeline once and return the completed state.

    `model` can be injected for testing, so the pipeline is testable offline.
    """
    state = ReasoningState(question=question)
    model = model or get_model()

    # Formalize and verify, with retries.
    _verify_with_reflection(model, state)

    # Only if we still could not decide: look for auxiliary evidence.
    if not state.verdict.was_verified:
        _gather_evidence(model, state)

    # Probabilistic reasoning about the final claim.
    state.reasoning = reason(model, state.claim)
    state.log("reason", f"{len(state.reasoning)} chars")

    # Explanation that separates verified fact, evidence, and reasoning.
    state.explanation = explain(
        model, state.claim, state.reasoning, state.verdict, state.subclaims
    )
    state.log("explain", f"after {len(state.attempts)} attempt(s)")

    return state
