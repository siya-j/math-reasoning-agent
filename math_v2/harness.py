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

SYNCHRONOUS OUTSIDE, ASYNCHRONOUS INSIDE
----------------------------------------
`prove()` stays synchronous because `scripts/evaluate_proofs.py` calls it that
way and is not being changed. Everything below it is async: every tool is an
`async def`, so LangChain builds a `StructuredTool` with a coroutine and NO
sync implementation.

Calling `agent.invoke()` on that graph fails the moment the model requests a
tool, with

    StructuredTool does not support sync invocation.

and — because the failure happens inside the graph — it looks like the agent
crashed rather than like a wiring fault: 0 model calls, 0 Lean calls, nothing
in the record. That is exactly what the first benchmark produced. So the bridge
below drives `ainvoke` and runs the coroutine itself.
"""

import asyncio
import concurrent.futures
import inspect
import tempfile
import time
from pathlib import Path

from domain.proof import Lemma, ProofAttempt, ProofRun, ProofStage, Telemetry
from domain.verdict import Verdict, VerificationStatus

from math_v2.context import MathContext
from math_v2.core import budget, log, verdict as verdicts
from math_v2.prompt import COMPUTE_ENV_GUIDANCE, MATH_SYSTEM_PROMPT
from math_v2.tools import _util, create_math_v2_tools

TASK = """{goal}

Settle this claim. If it needs a proof, formalise it in Lean 4 and prove it;
if a computation decides it, compute. Call `finish` when you are done, passing
the original question as `claim`."""

# Which records are ATTEMPTS AT A PROOF. A statement check is not one: it
# compiles `statement := by sorry` to find out whether the signature
# elaborates, and a PASS is reported by Lean as "compiles but uses sorry".
# Mapped to DIRECT, a successful check rendered as
#
#     attempt 1: direct
#     proof:                                    <- empty, it has no proof
#     compiler said: ... uses `sorry` ... proves nothing.
#
# which reads as a failed proof and is the reason a purely infrastructural
# failure looked like a reasoning failure. It also inflated `mean attempts`,
# a metric that is supposed to count tries at the goal.
_STAGE = {
    log.PROOF: ProofStage.DIRECT,
    log.LEMMA: ProofStage.DIRECT,
    log.SKELETON: ProofStage.SKELETON,
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
    # A reused workdir must not inherit another goal's compiles.
    _util.forget(workdir)

    if model is None and agent_factory is build_agent:
        from llm.client import get_model

        model = get_model()

    note("agent")
    prose = ""
    model_calls = 0
    deadline = budget.wall_clock_deadline()
    try:
        agent = agent_factory(model, create_math_v2_tools(),
                              MATH_SYSTEM_PROMPT + COMPUTE_ENV_GUIDANCE)
        result = _invoke(agent, goal, workdir, deadline)
        prose = _final_text(result)
        model_calls = _count_model_calls(result)
    except (asyncio.TimeoutError, TimeoutError):
        # THE OUTER WALL CLOCK. `budget.spend` samples the clock and is only
        # called from inside a tool, so time spent between tool calls — a model
        # call retrying with backoff, most of it — was invisible until the next
        # tool call, by which point it was gone. Measured: 1032s against a 300s
        # budget, ~700s of it inside one model call.
        #
        # Recorded through `budget.terminate` rather than as a new outcome, so
        # everything downstream is unchanged: `summary()` reports
        # `terminated_early`, `_to_proof_run` writes "stopped early: ..." into
        # the trace, and `eval.proof_metrics.classify` reads that and returns
        # EXHAUSTED. An agent that ran out of clock ran out of clock, however
        # the clock was read.
        budget.terminate(workdir, f"wall clock spent ({deadline:.0f}s)")
        log.note(workdir, f"stopped: wall clock spent ({deadline:.0f}s) — "
                          "the agent loop was abandoned")
    except Exception as exc:  # noqa: BLE001 - a crash must not lose the record
        # Everything the agent actually did is on disk already, so a harness
        # failure costs the prose and nothing else.
        log.note(workdir, f"agent failed: {exc}")

    return _to_proof_run(run, workdir, prose, time.monotonic() - started,
                         model_calls)


def _run_sync(coroutine):
    """Run a coroutine from synchronous code, with or without a live loop.

    `asyncio.run` refuses to nest inside a running loop, which happens when
    something upstream is already async. Falling back to a worker thread with
    its own loop keeps `prove()` callable from either world.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _takes_context(call):
    """Does this entry point accept `context=`? Checked, not guessed.

    A `try/except TypeError` around the call would also swallow a TypeError
    raised INSIDE the agent, turning a real failure into a silent retry
    without context — which is the class of bug this whole fix is about.
    """
    try:
        parameters = inspect.signature(call).parameters
    except (TypeError, ValueError):
        return True
    if "context" in parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


async def _ainvoke(agent, goal, workdir):
    """Drive the agent, preferring the async path its tools require.

    Every math_v2 tool is `async def`, so the compiled graph has no working
    sync path once a tool is called. A scripted test agent may still be
    synchronous, and is supported — but the real one always goes through
    `ainvoke`.
    """
    payload = {"messages": [{"role": "user", "content": TASK.format(goal=goal)}]}
    context = MathContext(workdir=workdir)

    call = getattr(agent, "ainvoke", None)
    if call is not None:
        if _takes_context(call):
            return await call(payload, context=context)
        return await call(payload)

    call = agent.invoke
    if _takes_context(call):
        return call(payload, context=context)
    return call(payload)


def _invoke(agent, goal, workdir, deadline=None):
    """Drive the agent, bounded by a real wall clock.

    `asyncio.wait_for` is applied INSIDE the coroutine that `_run_sync` runs,
    so it works on both paths — `asyncio.run` and the worker-thread fallback —
    without either needing to know about it.

    WHAT THIS DOES AND DOES NOT GUARANTEE, precisely. It abandons the agent
    loop: no further model call is awaited and no further tool runs. A Lean
    subprocess already in flight is NOT killed by this, because
    `asyncio.to_thread` cannot be cancelled — that one is bounded separately by
    the `timeout=` passed to `subprocess.run` in `_local.run`. So the true
    worst case is `deadline + _aura.DEFAULT_TIMEOUT`, which is finite and
    known, where before it was whatever the model SDK felt like doing.
    """
    if not deadline or deadline <= 0:
        return _run_sync(_ainvoke(agent, goal, workdir))

    async def bounded():
        return await asyncio.wait_for(_ainvoke(agent, goal, workdir),
                                      timeout=deadline)

    return _run_sync(bounded())


def _count_model_calls(result) -> int:
    """Assistant turns in the transcript — a real count, not a placeholder.

    It was hardcoded to 0, which reported "0 model" for a run that had plainly
    called the model. A number nobody can trust is worse than no number.
    """
    messages = (result or {}).get("messages") if isinstance(result, dict) else None
    if not messages:
        return 0
    return sum(
        1 for message in messages
        if getattr(message, "type", "") == "ai"
        or message.__class__.__name__ == "AIMessage"
        or (isinstance(message, dict) and message.get("role") == "assistant")
    )


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


def _to_proof_run(run: ProofRun, workdir: str, prose: str, seconds: float,
                  model_calls: int = 0) -> ProofRun:
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

    for record in log.records(workdir, log.STATEMENT_CHECK):
        run.trace.append(
            "statement check: "
            + ("elaborates" if record.get("status") == log.TRUE
               else "does NOT elaborate — " + (record.get("detail") or "")[:200])
        )

    attempts = [r for r in log.records(workdir) if r.get("kind") in _STAGE]
    for index, record in enumerate(attempts, start=1):
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
    # TWO DIMENSIONS, REPORTED SEPARATELY. `local+repl` would read as a third
    # execution mode; it is a Lean backend running inside the local one. An
    # A/B whose two arms cannot be told apart in the record is not an A/B.
    run.trace.append(f"execution mode: {_util.mode()}")
    run.trace.append(f"lean backend: {_util.lean_backend()}")
    # `reason`, NOT `terminated_early`. MEASURED on proofnet `exercise_1_2`:
    # the agent proved both helper lemmas, hit the compile limit, was told to
    # stop, and stopped cleanly on the first warning. `terminated` is only set
    # once the GRACE of 3 further calls is also spent, so no note was written
    # and `eval.proof_metrics.classify` fell through to NOT_PROVED — a budget
    # failure scored as a proving failure, in the proof-rate denominator.
    #
    # The perverse incentive is the point: an agent that ignored the stop and
    # burned three more calls was classified EXHAUSTED (excluded), while one
    # that obeyed it was classified NOT_PROVED (counted against). `reason` is
    # set the moment a limit blocks a call, and `_over` sets it only for the
    # time, tool and compile budgets — a search redirect never touches it.
    if spent["reason"]:
        run.trace.append(f"stopped early: {spent['reason']}")

    # Auxiliary lemmas. `ProofResult.lemmas_total` has always existed and was
    # always 0, because nothing populated `run.lemmas` — so "did decomposition
    # fire?" was unanswerable from the results file. Every KEPT lemma was
    # accepted by the compiler, hence a TRUE verdict; rejected ones stay in
    # `lemma_attempts` and are not offered here.
    for declaration in log.kept_lemmas(workdir):
        run.lemmas.append(Lemma(
            informal="",
            statement=declaration.split(":=")[0].strip(),
            proof=declaration,
            verdict=Verdict(VerificationStatus.TRUE, "lean", "kept"),
        ))

    run.telemetry = Telemetry(
        model_calls=model_calls,
        lean_calls=spent["lean_calls"],
        retrieval_calls=spent["searches"],
        symbolic_calls=spent["symbolic_calls"],
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
