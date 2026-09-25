"""Orchestration (Design Doc section 10) — the pipeline owns the flow.

    User Input
      -> Agent invocation        (model chooses tools)
      -> Guard                   (verdict from records + faithfulness lint)
      -> Reflection, if needed   (Phase 4: retry, bounded, in code)
      -> Decomposition, if still unverified  (Phase 5: auxiliary evidence)
      -> Final Response

The agent is a node inside this flow, not a replacement for it. This is the
design document's Principle 7 — every stage extends the previous system —
applied after an earlier rewrite replaced it instead.
"""

from __future__ import annotations

import config
from domain.attempt import Attempt, Strategy
from domain.state import AgentRun
from llm.client import get_model
from pipeline import cache, guard
from pipeline.agent import DECOMPOSE_INSTRUCTION, invoke_once
from pipeline.tools import VerificationLog
from pipeline.reflection import feedback_for, next_strategy


def run(question: str, model=None) -> AgentRun:
    """Run the full flow on one question and return an explicit record."""
    state = AgentRun(question=question)

    # A REPLAY, not a shortcut. The stored answer is the one THIS system
    # produced for THIS question under THIS configuration -- model, prover,
    # budget and backend are all in the key -- returned exactly as it was,
    # banner included. Nothing on this path can upgrade a verdict.
    #
    # It exists because the same question asked twice gave two answers:
    # variance is a measurement problem for a benchmark and a trust problem
    # for a user. Off unless MRA_CACHE names a directory.
    replayed = cache.load(question)
    if replayed is not None:
        state.answer = replayed.answer
        state.log("cache", f"replayed an answer stored at {replayed.stored_at}")
        return state

    model = model or get_model()

    # One log across every attempt, so computations survive the loop. They
    # are kept apart from checks on purpose: the guard turns checks into a
    # verdict about a claim, and a computation answers a question nobody
    # made a claim about. It must never be able to contribute to a TRUE.
    log = VerificationLog()

    # --- first pass -------------------------------------------------------
    checks, prose = invoke_once(model, question, log=log)
    verdict = guard.decide(question, checks)
    state.record(Attempt(1, Strategy.INITIAL, checks, verdict))

    # --- reflection (Phase 4) --------------------------------------------
    while len(state.attempts) < config.MAX_ATTEMPTS:
        strategy = next_strategy(verdict, state.attempts)
        if strategy is None:
            break

        state.log("reflect", strategy.value)
        checks, prose = invoke_once(
            model, question, feedback_for(strategy, verdict), log=log
        )
        verdict = guard.decide(question, checks)
        state.record(Attempt(len(state.attempts) + 1, strategy, checks, verdict))

    # --- decomposition (Phase 5) -----------------------------------------
    # Evidence only. It never changes the verdict; verified special cases do
    # not establish a general claim.
    if not verdict.was_verified and config.MAX_SUBCLAIMS:
        evidence, _ = invoke_once(model, question, DECOMPOSE_INSTRUCTION)
        state.evidence = evidence[: config.MAX_SUBCLAIMS]
        supported = sum(1 for c in state.evidence if c.verdict.was_verified)
        state.log(
            "decompose",
            f"{len(state.evidence)} auxiliary check(s), {supported} decided",
        )

    state.verdict = verdict
    state.answer = (
        f"{guard.banner(verdict, state.checks, state.evidence)}\n\n{prose}"
    )
    state.log("verdict", verdict.status.value)

    # Only a finished run is kept. `store` refuses one with no verdict, so a
    # crash or a timeout is never frozen into a permanent answer.
    if cache.store(question, state):
        state.log("cache", "stored")
    return state
