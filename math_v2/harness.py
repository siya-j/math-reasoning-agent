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
self-validation or narration middleware in this path, so what is measured is
tools + guard + model, not the production stack.

ONE PIECE OF MIDDLEWARE IS NOW PRESENT, and this paragraph used to say there
was none. `build_agent` installs `ContextEditingMiddleware`, which clears OLD
tool results once the input grows past a threshold. MEASURED on
eval/results/putnam-run4.json: 3,562,273 input tokens over five goals, about
29,700 per model call against a system prompt of roughly 4,000 -- so ~85% of
every call was conversation history re-sent, and the growth is why a hard goal
cost 16x the input of an easy one rather than 4x. `environment()` records
whether trimming was active and at what threshold, so a run with it cannot be
compared against one without it by accident.

WHY CLEARING IS SAFE HERE SPECIFICALLY, which is not a general claim: this
agent keeps its state on disk, and `proof_state` re-derives the whole picture
from `math/proof_log.json` in one uncharged call. A cleared tool result is
therefore recoverable by asking, unlike an agent whose only memory is its
transcript. `keep` also protects the most recent results, which is where the
goal state the model is actually working from lives.

THE AGENT MAY BE DRIVEN MORE THAN ONCE PER GOAL, and a model-call or token
count from here covers every pass. `_ainvoke` refuses a stop that the record
shows never tried the goal (see `MAX_CONTINUATIONS`), re-sending the
transcript with one deterministic instruction. It does NOT re-prod an agent
that tried and failed: whether stopping early with budget left is good
judgement or timidity is an open question in this repo, and a nudge that
assumes the answer would burn forty compiles on goals the agent read
correctly. The condition is the narrow one that was measured -- zero attempts.

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
import os
import concurrent.futures
import inspect
import tempfile
import time
from pathlib import Path

from domain.proof import Lemma, ProofAttempt, ProofRun, ProofStage, Telemetry
from domain.verdict import Verdict, VerificationStatus

from math_v2.context import MathContext
from math_v2.core import budget, preamble, log, verdict as verdicts
from math_v2.prompt import system_prompt
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
#
# `log.LEMMA` maps to LEMMA, not DIRECT. MEASURED, and it cost two metrics:
# collapsing helper attempts into DIRECT made them indistinguishable from
# tries at the goal, so `mean_attempts` counted them (b1 reported 33 attempts
# of which 7 were skeletons and at least 8 were lemmas), and every lemma
# attempt rendered in the trace as "attempt N: direct" for something that
# never touched the goal. It is the same information loss that made
# `via_synthesis` unreadable.
_STAGE = {
    log.PROOF: ProofStage.DIRECT,
    log.LEMMA: ProofStage.LEMMA,
    log.SKELETON: ProofStage.SKELETON,
}


# WHERE TO START CLEARING, chosen from run4's measured shape rather than left
# at the library default. That default is 100_000 tokens and this workload
# peaks around 55_000, so it would NEVER HAVE FIRED -- installing the
# middleware without moving this would have been a no-op that looked like a
# fix.
#
# Projected against run4's growth curve (4k at the first call rising to ~55k
# at the last, 24 calls per goal):
#
#     cap        per goal      saving
#     100,000     712,455          0%   never fires
#      40,000     672,000          6%
#      24,000     486,000         32%   <- default
#      16,000     354,000         50%
#      12,000     276,000         61%
#
# 24_000 is the conservative end on purpose: clearing begins only after about
# nine model calls, so short goals are untouched entirely and long ones keep
# a wide working window. Lower values save more and are worth measuring, which
# is why this is an env var and not a literal.
CONTEXT_TRIM_TRIGGER = int(os.getenv("MRA_CONTEXT_TRIM", "24000"))

# Tool results that are never cleared. `proof_state` is the agent's way back
# to orientation and is cheap to keep; `check_statement` is where it learns
# whether its own statement elaborated. Both are called rarely, so excluding
# them costs almost nothing and removes the two cases where a cleared result
# would be most confusing.
CONTEXT_TRIM_KEEP_TOOLS = ("proof_state", "check_statement")

# How many of the most recent tool results survive regardless. The goal state
# the model is working from is in the last one or two.
CONTEXT_TRIM_KEEP = int(os.getenv("MRA_CONTEXT_TRIM_KEEP", "3"))

# WHETHER THE MODEL'S OWN SUBMISSIONS ARE CLEARED TOO, and the measurement
# that changed this from False to True.
#
# `ClearToolUsesEdit` clears tool RESULTS. With `clear_tool_inputs=False` the
# ARGUMENTS survive -- and for this agent the arguments are the proofs. Over
# 81 surviving workdirs:
#
#     tool inputs  (statement + proof, KEPT)     700,997 chars  ~175k tokens
#     tool results (Lean's reply, CLEARED)       192,526 chars   ~48k tokens
#
# So trimming could only ever reclaim 22% of the tool traffic; the other 78%
# accumulated untouched. That is why trigger=24,000 still produced 69,805
# input tokens PER CALL on `exercise_4_5_22` and 174,637 on `exercise_3_22`
# -- 2.9x and 7.3x the trigger it was supposed to hold.
#
# WHY CLEARING THEM IS NOT FORGETTING. `proof_state` reports "the attempts
# the compiler rejected and why", costs no compile, and is in
# CONTEXT_TRIM_KEEP_TOOLS -- so it is never cleared. The submissions move
# from riding in every call to being available on request, which is what the
# prompt already tells the model to do after a rejection.
#
# The cost of the model forgetting anyway is bounded: a resubmitted proof is
# refused by byte-equality repeat detection, and refusals are free (they
# refund the Lean call). A wasted turn, not a wasted compile.
#
# An env var, not a literal, because the SAVING is unverified -- it needs a
# paid run to measure, and a run with this on is not comparable to one
# without. `context_policy()` records it.
CONTEXT_TRIM_INPUTS = os.getenv("MRA_CONTEXT_TRIM_INPUTS", "1") not in (
    "0", "false", "False", "no", "")


# A CEILING ON MODEL CALLS, WHICH IS THE ONLY THING THAT BOUNDS SPEND.
#
# Every other budget here counts ACTIONS -- compiles, searches, tool calls,
# wall clock. None of them counts the thing that is billed. MEASURED over 226
# goal-runs: model calls run at a median 1.3x the counted tool calls and up to
# 5x, so a goal can sit inside every existing limit and still make 83 model
# calls, which `exercise_2_5_30` did.
#
# AND THE COST IS QUADRATIC, because this path re-sends the whole history on
# every call (`harness`'s own docstring: no summarisation middleware). Fitted
# over the 70 goal-runs that carry both numbers:
#
#     input_tokens ~ 809.5 * model_calls^2
#     sanity: 83 calls -> 5,576,642 predicted against 5,175,349 actual
#
# So halving the calls quarters the cost, and a cap is worth far more than its
# call count suggests.
#
# FORTY, DERIVED RATHER THAN CHOSEN. Model calls by outcome:
#
#                  n   median   p90   p95   p99   max
#     PROVED     111        8    27    40    60    79
#     failed      71       20    37    57    83    83
#
#     cap 25 -> loses 12 of 111 proofs
#     cap 30 -> loses  9
#     cap 40 -> loses  4        <- p95 of proved
#     cap 50 -> loses  4        strictly worse: same proofs, 65 more wasted calls
#     cap 60 -> loses  1
#
# Forty is p95 of the proved distribution, and 50 is dominated by it -- the
# same four proofs lost for less saving. Applied to the failed runs above it,
# the estimated saving is 14.8M input tokens, 28% of everything this project
# has spent, for 3.6% of its proofs.
#
# `thread_limit`, NOT `run_limit`: `_continuation_allowance` re-drives the
# agent, so a per-invocation limit would reset on every continuation and bound
# nothing. `exit_behavior="end"` because the verdict is derived from the
# record -- ending leaves an honest partial result exactly as the wall-clock
# budget does, where `error` would lose the goal to an exception.
MAX_MODEL_CALLS = int(os.getenv("MRA_MAX_MODEL_CALLS", "40"))


def context_policy() -> dict:
    """What context management was in force, for the results file.

    Recorded for the same reason the Lean backend is: a number produced with
    trimming and one produced without it are not comparable, and nothing else
    in the record would say which this was.
    """
    return {
        "context_trimming": bool(CONTEXT_TRIM_TRIGGER),
        "context_trim_trigger": CONTEXT_TRIM_TRIGGER,
        "context_trim_keep": CONTEXT_TRIM_KEEP,
        "context_trim_inputs": CONTEXT_TRIM_INPUTS,
        # Recorded for the same reason as the trimming policy: a run bounded
        # at 40 model calls and one unbounded are not comparable, and nothing
        # else in the record would say which this was.
        "max_model_calls": MAX_MODEL_CALLS,
    }


def build_agent(model, tools, system_prompt):
    """The LangChain agent. Separate so a test can inject a scripted one."""
    from langchain.agents import create_agent

    middleware = []
    if CONTEXT_TRIM_TRIGGER > 0:
        from langchain.agents.middleware import (
            ClearToolUsesEdit,
            ContextEditingMiddleware,
        )

        middleware.append(ContextEditingMiddleware(edits=[ClearToolUsesEdit(
            trigger=CONTEXT_TRIM_TRIGGER,
            keep=CONTEXT_TRIM_KEEP,
            # The ARGUMENTS go too, by default. Keeping them capped what
            # trimming could reclaim at 22% of the tool traffic, because the
            # arguments here are the proofs -- see CONTEXT_TRIM_INPUTS.
            # `proof_state` is excluded from clearing and reports the
            # rejected attempts, so this is recoverable rather than lost.
            clear_tool_inputs=CONTEXT_TRIM_INPUTS,
            exclude_tools=CONTEXT_TRIM_KEEP_TOOLS,
        )]))

    if MAX_MODEL_CALLS > 0:
        from langchain.agents.middleware import ModelCallLimitMiddleware

        middleware.append(ModelCallLimitMiddleware(
            thread_limit=MAX_MODEL_CALLS, exit_behavior="end"))

    return create_agent(model=model, tools=tools, system_prompt=system_prompt,
                        context_schema=MathContext, middleware=middleware)


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
    # THE GOAL'S OWN `open` LINES, captured before the agent runs.
    #
    # They arrive inside `goal` -- ProofNet ships them in its header and the
    # text handed to the model includes them -- but the compiler only ever
    # saw `import Mathlib`. MEASURED: 43 of 702 submitted statements carried
    # an `open`, and `exercise_2_5_30` fails with `Function expected at card`
    # without `open Fintype` and elaborates with it. See
    # `math_v2/core/preamble.py`.
    preamble.remember(workdir, goal)

    if model is None and agent_factory is build_agent:
        from llm.client import get_model

        model = get_model()

    note("agent")
    prose = ""
    model_calls = 0
    tokens = (0, 0)
    # WHETHER THE COST NUMBERS MEAN ANYTHING. `model_calls` and `tokens` are
    # read off the agent's returned transcript, and on the timeout and crash
    # paths below there IS no returned transcript -- so they stay at their
    # initial zero for a run that certainly called the model.
    #
    # MEASURED on eval/results/putnam-run3.json: three of five goals reported
    # `model_calls: 0` and zero tokens while having made 1, 4 and 6 Lean calls
    # respectively. The run's summary then added up the two survivors and
    # presented 1,229,551 input tokens as the cost of five goals. Reading 0 as
    # "free" is the exact error `Telemetry`'s own comment warns about, arrived
    # at from the other direction: not a provider that reports nothing, but a
    # run whose report never came back.
    telemetry_complete = False
    deadline = budget.wall_clock_deadline()
    try:
        agent = agent_factory(model, create_math_v2_tools(),
                              system_prompt())
        result = _invoke(agent, goal, workdir, deadline)
        prose = _final_text(result)
        model_calls = _count_model_calls(result)
        tokens = _count_tokens(result)
        # A RETRIED RUN UNDER-REPORTS ITS COST. The crashed pass's usage died
        # with its transcript, so what came back covers only the pass that
        # succeeded. Reporting that as measured would be exactly the "zero
        # cost for work that certainly cost something" failure
        # `Telemetry.complete` was added to prevent.
        telemetry_complete = not any(
            entry.startswith("retried after transient failure")
            for entry in log.read(workdir).get("trace") or []
        )
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
        #
        # THE TYPE, NOT JUST THE MESSAGE. MEASURED on
        # eval/results/putnam-run3.json: two of five goals recorded the trace
        # line "agent failed: " -- with NOTHING after the colon, because
        # `str(exc)` is empty for plenty of real exceptions. Two runs died and
        # the record could not say what killed them, which made the whole run
        # unusable as a comparison. `repr` would be noisier and is worth it:
        # a class name alone already separates a rate limit from a decode
        # error from a cancelled task.
        log.note(workdir, f"agent failed: {type(exc).__name__}: {exc}".rstrip(": "))

    return _to_proof_run(run, workdir, prose, time.monotonic() - started,
                         model_calls, tokens, telemetry_complete)


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


# HOW MANY TIMES THE HARNESS WILL REFUSE TO ACCEPT THE AGENT'S DECISION TO
# STOP, when the record shows it never once tried the goal.
#
# MEASURED on `exercise_1_19` in eval/results/mixed-1.json. The agent wrote
# one skeleton, proved a helper lemma, then searched Mathlib for a name that
# does not exist (`nsmul_eq_smul_cast`) twice, got nothing both times, and
# ended its turn with the sentence "Wait, let's search for `nsmul_eq_smul_cast`
# in Mathlib." repeated fifteen times and no tool call at all. The graph ends
# when the model returns no tool calls, so the run was recorded `not_proved`
# with SEVEN of forty compiles spent, ZERO attempts at the goal, and 71,316
# output tokens of that repetition billed.
#
# It also never called `finish`, which `TASK` instructs it to call "once you
# are done, whatever happened". So this is not the agent judging the goal too
# hard -- there is no such judgement in the record. It is the loop falling out
# from under a degenerate turn, and the harness accepting silence as an answer.
#
# Bounded, and small. A model that will not try after two deterministic
# instructions is not going to try after ten, and every pass re-sends the
# transcript.
MAX_CONTINUATIONS = int(os.getenv("MRA_MAX_CONTINUATIONS", "2"))

# HOW MANY TIMES A TRANSPORT FAILURE IS RETRIED before the goal is given up on.
#
# MEASURED: `exercise_2_11_22` has died with `agent failed: ReadError` in three
# consecutive runs -- eval/results/mixed-1-rebuilt.json,
# failures-after-decompose.json and proofnet-20-after-soundness.json. In the
# last of those it had already spent 289 seconds and three compilations before
# the read failed. `eval.proof_metrics.classify` returns ERROR for it, which is
# right -- a run that died is evidence about nothing -- and the consequence is
# that the denominator has silently been 19 instead of 20 in every ProofNet
# number this project has quoted.
#
# A RETRY IS SOUND HERE FOR A SPECIFIC REASON, not by general optimism:
# everything the agent did is already on disk, and `proof_state` re-derives the
# whole picture from `math/proof_log.json` in one uncharged call. So a second
# pass resumes with the record intact even though the transcript is gone --
# which is the same property that makes context trimming safe in this path.
MAX_TRANSIENT_RETRIES = int(os.getenv("MRA_MAX_TRANSIENT_RETRIES", "2"))

# Seconds before retrying. Short, because the wall clock is a real budget and
# `_invoke` wraps the whole loop -- a retry spends the goal's remaining time,
# it does not extend it.
TRANSIENT_BACKOFF_SECONDS = float(os.getenv("MRA_TRANSIENT_BACKOFF", "5"))

# TRANSPORT AND SERVER FAULTS, BY TYPE NAME, and named rather than imported on
# purpose. The provider stack differs per model -- httpx under google_genai,
# something else under another provider -- and importing any of them here
# would tie this module to one. A name-based set is also honest about what it
# is: a list of things observed or documented to be transient, not a claim to
# have enumerated them.
#
# Deliberately NOT retried: anything else. A TypeError or an AttributeError is
# a wiring fault, and retrying it three times would turn a clear stack trace
# into a slow one.
_TRANSIENT = frozenset({
    "ReadError", "WriteError", "ConnectError", "ConnectTimeout",
    "ReadTimeout", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
    "ProtocolError", "IncompleteRead", "ServiceUnavailable",
    "InternalServerError", "DeadlineExceeded", "ResourceExhausted",
    "TooManyRequests", "Overloaded", "APIConnectionError", "APITimeoutError",
})

# EXHAUSTION THAT WAITING CANNOT FIX, matched in the message rather than the
# type because the provider reports it with the same class and status as an
# ordinary rate limit.
#
# MEASURED on eval/results/proofnet-60.json: a monthly spending cap arrives as
# `ChatGoogleGenerativeAIError ... (RESOURCE_EXHAUSTED): 429`, identical in
# type to a per-minute limit. `ResourceExhausted` is in the set above because
# a per-minute limit IS worth a backoff -- but a billing cap will not clear in
# five seconds, and retrying it twice per goal spent 132 seconds across three
# goals achieving nothing. Deep in a long run that is minutes of waiting for
# a wall that is not moving.
#
# A substring match on someone else's message is fragile, and it is used here
# only to make a retry FAIL FASTER -- never to decide an outcome. If the
# wording changes, the behaviour degrades to what it was: a pointless retry,
# not a wrong answer.
# MOVED TO `llm/exhaustion.py`, and re-exported here because every script
# that calls a model needs the same distinction and this project has paid
# three times for one fact living in two hand-maintained places. The
# behaviour is unchanged; `tests/test_transient_retry.py` guards it and
# `tests/test_exhaustion.py` fails if a private copy reappears here.
from llm.exhaustion import is_hopeless as _is_hopeless  # noqa: E402


def _is_transient(exc):
    """Is this a transport or server fault rather than a bug?

    Walks `__cause__`/`__context__` because provider SDKs wrap: the ReadError
    that killed `exercise_2_11_22` arrives inside whatever the client raises,
    and matching only the outermost type would miss it.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if type(exc).__name__ in _TRANSIENT:
            return True
        exc = exc.__cause__ or exc.__context__
    return False

# Below this many compiles left, a continuation cannot accomplish anything: a
# rejected attempt costs one and the repair it suggests costs another.
CONTINUATION_LEAN_FLOOR = 2

# HOW MANY ATTEMPTS AT THE GOAL COUNT AS HAVING ENGAGED WITH IT, and below how
# much spent budget a stop is treated as premature.
#
# This guard began as "zero attempts", and the repo deliberately declined to go
# further: whether stopping early with budget left is good judgement or
# timidity was recorded as an OPEN QUESTION, with `Telemetry.lean_budget` added
# to collect the evidence. The evidence is now in.
#
# MEASURED on eval/results/proofnet-20-after-soundness.json. Of five unproved
# goals, three stopped after ONE attempt with almost nothing spent:
#
#     exercise_3_1    1 attempt   5/40 compiles (12%)
#     exercise_1_19   1 attempt   3/40 compiles ( 8%)
#     exercise_5_37   1 attempt   2/40 compiles ( 5%)
#
# and NOT ONE GUARD FIRED on any of them -- the decomposition redirect needs
# three failed attempts, the assembly guard needs three kept lemmas, and this
# one needed zero. Nothing covered "stopped after one attempt with 92% of the
# budget left".
#
# The other two failures are the opposite case and must stay untouched:
# `exercise_2_5_30` (6 attempts, 68% spent) and `exercise_4_5_22` (7 attempts,
# 55% spent) fought hard and lost, and between them ate half the run's 20.7M
# input tokens. Prodding those buys nothing.
#
# TWO, RE-DERIVED. The number was three, from "83% of every proof this system
# has landed arrived within three attempts" -- and that curve was computed
# over every PROOF record, 35% of which are `try_standard_tactics` writing the
# tactic ladder rather than the model. Counting a machine attempt as
# engagement is what this guard exists to refuse, so the threshold behind it
# was calibrated on the wrong population.
#
# RE-MEASURED over the 46 runs in `eval/evidence/` that produced an accepted
# proof, counting MODEL attempts only:
#
#     within 1 attempt    34/46   74%        (mixed curve: 43%)
#     within 2 attempts   39/46   85%        (mixed curve: 80%)
#     within 3 attempts   39/46   85%        (mixed curve: 85%)
#
# The 85% that justified three on the mixed curve arrives at TWO on this one,
# and the third attempt adds nothing at all -- 85% to 85%. So two is the same
# standard, applied to the population the guard actually means.
#
# Five of those 46 proofs (11%) arrived with ZERO model attempts: the ladder
# closed them outright. That is why the ladder is marked rather than removed.
ENGAGEMENT_FLOOR = int(os.getenv("MRA_ENGAGEMENT_FLOOR", "2"))

# Half the compile budget. Above it the agent has committed to the goal
# whatever its attempt count, and a nudge second-guesses work rather than
# prompting it.
ENGAGEMENT_BUDGET_FRACTION = 0.5

CONTINUE = """STOP.

{what} The record shows {left} of {total} compilations unused and {searches} searches already spent.

This is not a request to search more. Searching is not proving. Call `try_proof` on THE GOAL now, with the best proof you can write from what you already have. An attempt the compiler rejects costs one compilation and tells you exactly where the difficulty is; zero attempts tell you nothing and score nothing. If it then turns out you genuinely cannot proceed, call `finish` and say so."""


def _why_short(workdir):
    """The opening sentence of the continuation, naming what the record shows.

    Two cases, because the instruction differs. Zero attempts is a turn that
    fell out from under the agent -- on `exercise_1_19` it ended with one
    sentence repeated fifteen times. One or two attempts with the budget
    untouched is a different thing: it tried, and stopped well short of where
    attempts stop converting.
    """
    attempts = _model_attempts(workdir)
    if not attempts:
        return ("You ended your turn without submitting a single attempt at "
                "the goal, and without calling `finish`.")
    return (f"You stopped after {attempts} attempt(s) at the goal. Of every "
            "proof this system has landed, half arrived on the FIRST attempt "
            "and 83% within three, so this is well short of where attempts "
            "stop paying rather than past it.")


def _model_attempts(workdir) -> int:
    """Attempts at the goal THE MODEL made, excluding the tactic ladder.

    `try_standard_tactics` logs `kind=PROOF` like any other attempt, and 35%
    of all proof records are its. Counting them lets the system answer "has
    the model engaged with this goal" on the model's own behalf -- the exact
    substitution `log.Record.auto` was introduced to prevent for synthesised
    lemmas. Records written before that flag existed read as falsy, i.e. as
    the model's work, which is the safe direction: it under-fires rather than
    prodding a goal that was genuinely worked.
    """
    return sum(1 for record in log.records(workdir, log.PROOF)
               if not record.get("auto"))


def _stopped_short(workdir):
    """Compiles left if the agent stopped without engaging the goal.

    Read from the RECORD -- `log.PROOF` entries and the budget file -- and
    never from the final message, for the reason the final message is
    untrustworthy in exactly this case: on `exercise_1_19` it was fifteen
    repetitions of one sentence. Prose cannot be asked whether prose is
    degenerate.

    SKELETON deliberately does not count as trying. A skeleton compiles the
    goal with `sorry` holes; it establishes that a plan is well formed and
    proves nothing, which is the same rule `eval.proof_metrics._AT_THE_GOAL`
    already applies to the same records. `exercise_1_19` had written one.

    Returns 0 when a continuation is not warranted, so the caller reads it as
    "how much room is there", not as two separate questions.
    """
    goal = log.declared_goal(workdir)
    if goal and log.accepted_proof(workdir, goal):
        return 0
    # A COMPILED REFUTATION IS A RESULT, not a failure to prove, so a run that
    # produced one is finished and must not be prodded back at the goal.
    if verdicts.verified_refutation(workdir):
        return 0

    attempts = _model_attempts(workdir)
    if attempts >= ENGAGEMENT_FLOOR:
        return 0

    spent = budget.read(workdir)["lean_calls"]
    if spent > budget.MAX_LEAN_CALLS * ENGAGEMENT_BUDGET_FRACTION:
        return 0

    left = budget.headroom(workdir)["lean_calls_left"]
    return left if left >= CONTINUATION_LEAN_FLOOR else 0


async def _one_pass(agent, payload, context):
    """One trip through the agent graph, on whichever entry point it exposes.

    Every math_v2 tool is `async def`, so the compiled graph has no working
    sync path once a tool is called. A scripted test agent may still be
    synchronous, and is supported — but the real one always goes through
    `ainvoke`.
    """
    call = getattr(agent, "ainvoke", None)
    if call is not None:
        if _takes_context(call):
            return await call(payload, context=context)
        return await call(payload)

    call = agent.invoke
    if _takes_context(call):
        return call(payload, context=context)
    return call(payload)


async def _with_retries(agent, payload, context, workdir):
    """One pass, retried on a transport fault. Anything else propagates.

    MEASURED: `exercise_2_11_22` died of `ReadError` in three consecutive runs
    and was excluded from every rate, so the ProofNet denominator has silently
    been 19 rather than 20. See `MAX_TRANSIENT_RETRIES`.

    THE RECORD SURVIVES A CRASH, which is what makes this a resume rather than
    a restart: the proof log is on disk, and the retried pass can call
    `proof_state` to recover everything the lost transcript contained.

    Each retry is NOTED, and the note is what marks the cost incomplete. The
    crashed pass's own token usage died with its transcript, so a run that
    retried under-reports what it spent -- and reporting that as a measured
    total would be the "zero cost for work that certainly cost something"
    failure `Telemetry.complete` exists to prevent.
    """
    for attempt in range(MAX_TRANSIENT_RETRIES + 1):
        try:
            return await _one_pass(agent, payload, context)
        except Exception as exc:  # noqa: BLE001 - re-raised below unless transient
            last = attempt >= MAX_TRANSIENT_RETRIES
            if not _is_transient(exc) or _is_hopeless(exc) or last:
                raise
            log.note(workdir,
                     f"retried after transient failure: {type(exc).__name__}"
                     f" (attempt {attempt + 1} of {MAX_TRANSIENT_RETRIES})")
            await asyncio.sleep(TRANSIENT_BACKOFF_SECONDS)


async def _ainvoke(agent, goal, workdir):
    """Drive the agent, and refuse a stop that never tried the goal.

    The continuation re-sends the transcript with one deterministic
    instruction appended. The instruction is written HERE, by the harness,
    from the record -- the model is told what it spent, not asked what it
    thinks it spent.

    Wall clock: this whole loop runs inside the `asyncio.wait_for` that
    `_invoke` wraps around it, so continuations cannot extend the deadline.
    They spend the remaining budget rather than adding to it.
    """
    payload = {"messages": [{"role": "user", "content": TASK.format(goal=goal)}]}
    context = MathContext(workdir=workdir)
    result = await _with_retries(agent, payload, context, workdir)

    for _ in range(MAX_CONTINUATIONS):
        left = _stopped_short(workdir)
        if not left:
            break
        messages = list((result or {}).get("messages") or []) \
            if isinstance(result, dict) else []
        if not messages:
            # Nothing to continue FROM. A scripted agent that returns no
            # transcript gets its result back untouched rather than a second
            # cold call it never asked for.
            break
        spent = budget.read(workdir)
        log.note(workdir, "refused the stop: no attempt at the goal, "
                          f"{left} compiles left")
        payload = {"messages": messages + [{
            "role": "user",
            "content": CONTINUE.format(
                what=_why_short(workdir),
                left=left,
                total=budget.MAX_LEAN_CALLS,
                searches=spent.get("searches", 0)),
        }]}
        try:
            result = await _with_retries(agent, payload, context, workdir)
        except Exception as exc:
            # A CONTINUATION MUST NOT COST THE RUN ITS RESULT. This pass is
            # extra work the harness chose to do, on top of a goal that had
            # already finished cleanly; if it throws, the honest outcome is
            # still the one the first pass earned. Without this, `prove`
            # records "agent failed" and `classify` returns ERROR, so a
            # legitimate `not_proved` is thrown away by the machinery meant
            # to improve it -- and every goal in the run carries that risk.
            #
            # `except Exception` deliberately, not BaseException: a wall-clock
            # expiry reaches this frame as `CancelledError`, which must
            # propagate so `_invoke`'s `wait_for` still bounds the run.
            log.note(workdir, f"continuation failed: {type(exc).__name__}: "
                              f"{exc}".rstrip(": "))
            break

    return result


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


def _count_tokens(result) -> tuple:
    """(input, output) tokens over the transcript, or (0, 0) if unreported.

    WHY, given `_count_model_calls` already exists: a call count is a poor
    proxy for money on this path. `build_agent` keeps the whole message
    history, so every call carries each prior turn and the input side grows
    with the run -- MEASURED on eval/results/putnam-run2.json, 13 calls on the
    goal that bailed early against 53 on the hardest, which is 4x the calls
    but on the order of 16x the input once growth is counted. The user's
    binding constraint on this project is the API bill, and nothing in the
    repo could read it.

    Reads `usage_metadata`, the LangChain-standard field, and tolerates its
    absence: a provider that does not report usage, or a scripted test agent
    whose messages carry none, yields (0, 0) rather than raising. Deliberately
    NOT an estimate from string lengths -- a fabricated token count that looks
    authoritative is worse than an honest zero, and `Telemetry` reports 0 as
    unknown rather than as free.
    """
    messages = (result or {}).get("messages") if isinstance(result, dict) else None
    if not messages:
        return 0, 0

    read_in = read_out = 0
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if usage is None and isinstance(message, dict):
            usage = message.get("usage_metadata")
        if not isinstance(usage, dict):
            continue
        read_in += usage.get("input_tokens") or 0
        read_out += usage.get("output_tokens") or 0
    return read_in, read_out


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
                  model_calls: int = 0, tokens: tuple = (0, 0),
                  telemetry_complete: bool = True) -> ProofRun:
    """Translate the on-disk record into a ProofRun. THE VERDICT IS RE-DERIVED.

    `finish`'s own reply is not consulted. The outcome is computed here from
    the same records `finish` reads, so a harness bug cannot promote a claim
    the guard would have refused — the guard's authority does not depend on
    anything downstream believing it.
    """
    # `declared_goal` ALONE decides the verdict — no fallback here. A proof
    # tool may compile against a one-off diversion ("something other than the
    # current statement") without that diversion becoming what the run is
    # scored against, and `proof_verdict` now refuses outright on an empty
    # statement rather than let `log.accepted_proof`'s "no filter" behaviour
    # on "" match any accepted proof. Falling back to `current_goal` HERE
    # would silently defeat that: it would hand `proof_verdict` a non-empty
    # statement (the diversion) whenever `check_statement` was never called,
    # and the new empty-statement guard would never get the chance to fire —
    # the same soundness gap, reopened one call up.
    declared = log.declared_goal(workdir)
    decision = verdicts.proof_verdict(workdir, declared)
    spent = budget.summary(workdir)

    # The fallback belongs HERE ONLY — a human-readable value for the trace
    # and report, never fed back into scoring.
    run.statement = declared or log.current_goal(workdir)
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
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        complete=telemetry_complete,
        # The ceiling, so "how much was left" is readable from the record.
        lean_budget=budget.MAX_LEAN_CALLS,
        # Which guards fired, and how often. Read from the budget file, where
        # the tool seam counts them -- the guards write nothing to the proof
        # log, so this is the only place the answer exists.
        refusals=dict(spent.get("refusals") or {}),
    )

    # RETAINED WHETHER OR NOT THE OUTCOME IS `refuted`, because the record is
    # the authority on that and this is only transcribing it. `classify`
    # decides REFUTED from the trace note; this puts the artefact next to it so
    # the claim can be rechecked. Empty dict when there is no accepted
    # refutation, which is the ordinary case.
    refutation = verdicts.verified_refutation(workdir)
    if refutation:
        run.refutation = refutation.get("proof", "")
        run.refutation_statement = refutation.get("statement", "")

    if decision["outcome"] == verdicts.PROVED:
        run.proof = decision["evidence"].get("proof", "")
        run.verdict = Verdict(VerificationStatus.TRUE, "lean",
                              decision["evidence"].get("detail", ""))
        return run

    stopped = f" Stopped early: {spent['reason']}." if spent["terminated_early"] else ""
    run.verdict = Verdict(
        VerificationStatus.UNKNOWN, "prover",
        decision["reason"] + stopped
        # 1200, not 200. MEASURED on eval/results/putnam-run4.json: all three
        # unproved goals FINISHED VOLUNTARILY with most of their compile
        # budget unused -- 2 of 40, 6 of 40, 19 of 40 -- and the only record
        # of WHY was this string, cut off mid-sentence. "The agent reported:
        # The Putnam 1962 A4 theorem is mathematically true. ### Mathematical
        # Analysis The standard proof of this claim uses Taylor's theor" is
        # not enough to tell a correct judgement (Mathlib lacks the machinery)
        # from a premature one (it had 21 compiles left).
        #
        # Prose is shown to a human and never read by the guard, so length
        # costs nothing but bytes. It is still bounded: this is a diagnostic,
        # not a transcript.
        + (f" The agent reported: {prose[:1200]}" if prose else ""),
    )
    return run
