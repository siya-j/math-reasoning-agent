"""The agentic prover: one conversation, tools, persistent state.

    MRA_PROVER=agentic

An EXPERIMENTAL alternative to pipeline/prover.py, which is unchanged. Both
are selectable so the comparison is measurable rather than assumed — the
lesson from the v2 experiment, where handing control flow to the model cost
coverage and nobody could say by how much until it was measured.

WHAT DIFFERS FROM THE BASELINE
------------------------------
The baseline is a fixed sequence of stateless calls. Here the model holds one
conversation and decides for itself when to search Mathlib, when to compile,
what to make of the goal state, and whether to search again — with everything
it has learned still in context.

That addresses the three things the baseline cannot do:
  * ask for another lemma after seeing why the first failed
  * remember that attempt 2 failed for the reason attempt 4 would repeat
  * interleave retrieval and compilation instead of doing all of one first

WHAT DOES NOT DIFFER
--------------------
The guard. A proof is established by a RECORDED compilation, never by the
agent saying so. `ProofLog.accepted` is the only path to TRUE, exactly as
`guard.decide` reads the verification log and not the agent's prose.
"""

from __future__ import annotations

import time

import config
from domain.proof import ProofRun, ProofStage
from domain.verdict import Verdict, VerificationStatus
from llm.client import get_model
from llm.formalizer import Formalizer
from pipeline.harness import build_agent, final_text
from pipeline.proof_tools import Budget, BudgetExhausted, ProofLog, make_proof_tools
from retrieval.loogle import LoogleSearch

SYSTEM_PROMPT = """You are proving a theorem in Lean 4 using Mathlib.

You have three tools. Use them in whatever order the problem demands.

Strategy that works:
1. Search Mathlib before writing anything. Most goals of this kind are
   already a theorem in the library, and citing one beats reconstructing it.
2. Try the standard tactics early. They are cheap and often sufficient.
3. When you compile and it fails, READ THE GOAL STATE. It tells you exactly
   what remains. Change your approach in response to it rather than
   resubmitting a variation of the same proof.
4. If a lemma you wanted does not exist under the name you guessed, search
   again with a different pattern rather than guessing a second name.
5. If the whole proof resists you, prove intermediate steps with `have` and
   assemble them.

Rules:
- `sorry` and `admit` compile and prove nothing. Never use them.
- Never introduce an `axiom`, and never leave `exact?` or `apply?` in a proof.
- Mathlib's argument order is often not the obvious one. Prefer the exact
  signature a search returned over what you remember.
- A proof only counts once `try_proof` reports ACCEPTED. Saying you are
  finished does not make it so.

Stop as soon as `try_proof` accepts something."""

TASK = """Prove this theorem.

{statement}

It was formalised from this claim: {goal}"""


def prove(
    goal: str,
    formalizer: Formalizer | None = None,
    check=None,
    model=None,
    search=None,
    reviewer=None,
    progress=None,
    agent_factory=build_agent,
    budget=None,
    **_ignored,
) -> ProofRun:
    """Attempt a proof by conversation. Everything is injected for testing."""
    from pipeline.prover import _succeed, lean_check

    started = time.monotonic()
    check = check or lean_check
    formalizer = formalizer or Formalizer()

    def note(stage: str) -> None:
        if progress:
            progress(stage)

    run = ProofRun(goal=goal)

    # Formalisation is unchanged: one call, before the conversation starts.
    # It measured 100%, so there is nothing here to fix.
    note("formalising")
    run.statement = formalizer.statement(goal)
    run.telemetry.model_calls += 1
    if not run.statement.strip():
        run.verdict = Verdict(
            VerificationStatus.UNKNOWN, "prover",
            "The claim could not be stated formally.",
        )
        return run
    run.log("formalise", run.statement)

    if search is None and config.RETRIEVAL_ENABLED:
        search = LoogleSearch()

    # An agent with the wheel can also drive in circles. A near-mathlib goal
    # ran without terminating and had to be interrupted by hand, leaving no
    # proof, no verdict and no record. Termination is now a property of the
    # code rather than a hope about the model.
    log = ProofLog(
        statement=run.statement,
        telemetry=run.telemetry,
        budget=budget
        or Budget(
            max_tool_calls=config.MAX_AGENT_STEPS,
            max_lean_calls=config.MAX_AGENT_LEAN_CALLS,
            max_searches=config.MAX_AGENT_SEARCHES,
            max_consecutive_searches=config.MAX_CONSECUTIVE_SEARCHES,
            max_seconds=config.MAX_AGENT_SECONDS,
        ),
    )
    tools = make_proof_tools(log, check, search)

    note("agent")
    # Build a real model only when the real harness is going to use one. An
    # injected factory brings its own, and a test must not need an API key.
    if model is None and agent_factory is build_agent:
        model = get_model()

    try:
        agent = agent_factory(model, tools, SYSTEM_PROMPT)
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": TASK.format(statement=run.statement, goal=goal),
                    }
                ]
            }
        )
        prose = final_text(result)
    except BudgetExhausted as exc:
        # The agent kept calling tools after being told to stop. Unwinding
        # here is what guarantees the run ends; everything recorded so far
        # is kept, including a proof if one was already accepted.
        prose = ""
        log.trace.append(f"stopped: {exc}")
    except Exception as exc:  # noqa: BLE001 - a crash must not lose the record
        prose = ""
        log.trace.append(f"agent failed: {exc}")

    run.attempts = log.attempts
    run.trace.extend(log.trace)
    run.telemetry.seconds = time.monotonic() - started

    # THE GUARD. Only a recorded compilation can establish a proof; the
    # agent's prose is never consulted.
    accepted = log.accepted
    if accepted is not None:
        return _succeed(run, accepted.proof, accepted.verdict, reviewer)

    # Distinguish "tried and failed" from "ran out of budget". They are
    # different results and conflating them would misreport a proof rate.
    stopped = f" Stopped early: {log.budget.reason}." if log.budget.reason else ""
    run.verdict = Verdict(
        VerificationStatus.UNKNOWN,
        "prover",
        f"No proof found in {len(log.attempts)} compilation(s).{stopped} "
        "Failure to find a proof is not evidence that the claim is false."
        + (f" The agent reported: {prose[:200]}" if prose else ""),
    )
    return run
