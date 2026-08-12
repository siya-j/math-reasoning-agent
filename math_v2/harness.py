"""Run one evaluation goal through math_v2 and return a ProofRun.

NO `from __future__ import annotations`. This module defines no `@tool` and
no tool module imports it, so §5.1 would permit it — but the allow-list is
kept to the three files the blueprint actually names. Widening it for
convenience is how the repo's most common failure gets in, and PEP 604
annotations work natively anyway.

WHAT THIS IS
------------
An ADAPTER, not a second agent. It exists so the existing evaluation harness
can drive `math_v2` without any change to `scripts/evaluate_proofs.py`, which
already dispatches on `config.PROVER` and knows nothing about provers.

    evaluate_proofs.py -> pipeline.proving.prove -> here -> the agent

WHY `create_agent` AND NOT `create_deep_agent`
----------------------------------------------
Evaluation needs the tools, the guard, the budget and the lint. It does not
need the Aura middleware stack, and requiring `deepagents` plus the whole
framework to answer "did this regress?" would put the measurement out of reach
on every machine that has the goals.

Verified: `create_agent` accepts the sixteen tools with
`context_schema=MathContext`, and `ToolRuntime` injection is a LangChain
feature rather than a deepagents one.

STATE THIS WHENEVER A NUMBER FROM HERE IS QUOTED. There is no
summarisation, self-validation or narration middleware in this path, so what
is measured is tools + guard + model, not the production stack.

FRESH WORKSPACE PER GOAL
------------------------
The proof log and the budget both live in the workspace. Reusing one directory
would let goal 2 inherit goal 1's spent budget and — far worse — its accepted
proofs, so `accepted_proof` could match the wrong goal. One directory per goal,
always.
"""

import asyncio
import tempfile
import time
from pathlib import Path

from domain.proof import ProofAttempt, ProofRun, ProofStage, Telemetry
from domain.verdict import Verdict, VerificationStatus

from math_v2.context import MathContext
from math_v2.core import budget, log, verdict as verdicts
from math_v2.prompt import COMPUTE_ENV_GUIDANCE, MATH_SYSTEM_PROMPT
from math_v2.tools import _util, create_math_v2_tools

TASK = """{goal}

Settle this claim. If it needs a proof, formalise it in Lean 4 and prove it;
if a computation decides it, compute. Call `finish` when you are done, passing
the original question as `claim`."""

_STAGE = {
    log.PROOF: ProofStage.DIRECT,
    log.LEMMA: ProofStage.DIRECT,
    log.SKELETON: ProofStage.SKELETON,
    log.STATEMENT_CHECK: ProofStage.DIRECT,
}


def build_agent(model, tools, system_prompt):
    """The LangChain agent. Separate so a test can inject a scripted one."""
    from langchain.agents import create_agent

    return create_agent(model=model, tools=tools, system_prompt=system_prompt,
                        context_schema=MathContext)


def prove(
    goal: str,
    model=None,
    workdir: str | None = None,
    agent_factory=build_agent,
    progress=None,
    **_ignored,          # depth, reviewer: the old prover's, not ours
) -> ProofRun:
    """Settle one claim and report it in the shape the evaluator expects."""
    started = time.monotonic()
    run = ProofRun(goal=goal)

    def note(stage: str) -> None:
        if progress:
            progress(stage)

    workdir = workdir or tempfile.mkdtemp(prefix="mathv2_")
    Path(workdir).mkdir(parents=True, exist_ok=True)
    log.clear(workdir)
    budget.reset(workdir)

    if model is None and agent_factory is build_agent:
        from llm.client import get_model

        model = get_model()

    note("agent")
    prose = ""
    try:
        agent = agent_factory(model, create_math_v2_tools(),
                              MATH_SYSTEM_PROMPT + COMPUTE_ENV_GUIDANCE)
        result = _invoke(agent, goal, workdir)
        prose = _final_text(result)
    except Exception as exc:  # noqa: BLE001 - a crash must not lose the record
        # Everything the agent actually did is on disk already, so a harness
        # failure costs the prose and nothing else.
        log.note(workdir, f"agent failed: {exc}")

    return _to_proof_run(run, workdir, prose, time.monotonic() - started)


def _invoke(agent, goal, workdir):
    payload = {"messages": [{"role": "user", "content": TASK.format(goal=goal)}]}
    context = MathContext(workdir=workdir)
    try:
        return agent.invoke(payload, context=context)
    except TypeError:
        # A scripted test agent may not take `context`.
        return agent.invoke(payload)


def _final_text(result) -> str:
    """The assistant's last message. Prose is shown to a human and never read
    by the guard, so failing to extract it must not fail the run."""
    if isinstance(result, str):
        return result
    messages = (result or {}).get("messages") if isinstance(result, dict) else None
    if not messages:
        return ""
    last = messages[-1]
    return (getattr(last, "text", None) or getattr(last, "content", "")
            or (last.get("content", "") if isinstance(last, dict) else "")) or ""


def _to_proof_run(run: ProofRun, workdir: str, prose: str, seconds: float) -> ProofRun:
    """Translate the on-disk record into a ProofRun. THE VERDICT IS RE-DERIVED.

    `finish`'s own reply is not consulted. The outcome is computed here from
    the same records `finish` reads, so a harness bug cannot promote a claim
    the guard would have refused — the guard's authority does not depend on
    anything downstream believing it.
    """
    statement = log.current_goal(workdir)
    decision = verdicts.proof_verdict(workdir, statement)
    spent = budget.summary(workdir)

    run.statement = statement
    run.statement_ok = decision["outcome"] != verdicts.NOT_FORMALIZED

    for index, record in enumerate(log.records(workdir), start=1):
        status = {
            log.TRUE: VerificationStatus.TRUE,
            log.FALSE: VerificationStatus.FALSE,
        }.get(record.get("status"), VerificationStatus.UNKNOWN)
        # Only a PROOF record may carry TRUE into the run: that is the same
        # rule the guard applies, restated where the evaluator can see it.
        if record.get("kind") != log.PROOF and status is VerificationStatus.TRUE:
            status = VerificationStatus.UNKNOWN
        run.attempts.append(ProofAttempt(
            index,
            _STAGE.get(record.get("kind"), ProofStage.DIRECT),
            record.get("proof", ""),
            Verdict(status, "lean", record.get("detail", "")),
        ))

    run.trace.extend(log.read(workdir)["trace"])
    run.trace.append(f"execution mode: {_util.mode()}")
    if spent["terminated_early"]:
        run.trace.append(f"stopped early: {spent['reason']}")

    run.telemetry = Telemetry(
        model_calls=0,                       # not observable from the record
        lean_calls=spent["lean_calls"],
        retrieval_calls=spent["searches"],
        seconds=seconds,
    )

    if decision["outcome"] == verdicts.PROVED:
        run.proof = decision["evidence"].get("proof", "")
        run.verdict = Verdict(VerificationStatus.TRUE, "lean",
                              decision["evidence"].get("detail", ""))
        return run

    stopped = f" Stopped early: {spent['reason']}." if spent["terminated_early"] else ""
    run.verdict = Verdict(
        VerificationStatus.UNKNOWN, "prover",
        decision["reason"] + stopped
        + (f" The agent reported: {prose[:200]}" if prose else ""),
    )
    return run
