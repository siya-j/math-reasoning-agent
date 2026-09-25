"""A model-call cap that survives a continuation.

NO `from __future__ import annotations` — this module is imported alongside
`core/`, and the ToolRuntime injection that rule protects is one import away.

THE FAILURE THIS EXISTS FOR
---------------------------
`MRA_MAX_MODEL_CALLS` did not bound a run. It bounded a pass.

LangChain's `ModelCallLimitMiddleware` keeps `thread_model_call_count` in
graph state, and a `thread_limit` accumulates across invocations only when a
checkpointer and a stable `thread_id` carry that state between them.
`harness._one_pass` calls `ainvoke(payload, context=context)` with no config,
no `thread_id` and no checkpointer, so every continuation began a fresh count
and `harness._ainvoke` allows `MAX_CONTINUATIONS` of them. The real ceiling
was `(MAX_CONTINUATIONS + 1) x` the cap — 120 model calls at the default 40,
not 40.

MEASURED, on eval/results/heldout-restart-20.json with the cap set to 20. The
split is exact, and it is the whole argument for this file:

    continuations   model calls
    0 (six goals)   19, 21, 21, 21, 21, 21
    1-2 (four)      23, 28, 28, 29

`Dummit-Foote_exercise_4_5_17` is the clean demonstration: capped at 20, it
spent 28 — more than it spent in the run that was capped at 40.

WHY THE COUNT LIVES IN THE BUDGET FILE
--------------------------------------
The alternative is a checkpointer plus `thread_id`, and it is the wrong one
here: with state persisted under one thread, an `ainvoke` carrying
`{"messages": [...]}` APPENDS to the stored messages, and the continuation in
`_ainvoke` already re-sends the entire prior transcript. Every continuation
would then double its own history — a correctness change to the transcript in
service of a counter.

The budget file has the properties needed and no others: it is per-goal, it
already survives the pass boundary, `budget.reset` zeroes it when a goal
starts, and nothing else reads it. This is the same reason the compile,
search and symbolic counters live there rather than in the agent.

THE LIMIT IS NOT POLICY THIS MODULE OWNS. It arrives as a constructor
argument, so `harness` remains the one place that decides what the cap is and
the one place that records it into the results file.
"""

from math_v2.core import budget

# LangChain is imported inside `limiter()`, not here. `harness` imports
# this module at module scope for `is_limit_notice`, and `build_agent`'s
# other middleware imports are all lazy for the same reason: the offline
# test suite and the cold-import checks must not pay for a framework they
# never drive.

# Stamped on the message this middleware injects when it ends a run, so the
# transcript can be counted honestly. Without it `harness._count_model_calls`
# scores that synthetic turn as a model call and reports 21 against a cap of
# 20 — the off-by-one that first exposed the bug above. A marker, not a
# content match: the message text is a human-readable string and must stay
# free to change.
LIMIT_MARKER = "mra_model_call_limit"


def is_limit_notice(message):
    """True for the synthetic turn this middleware injects. Never a real call.

    Tolerates anything a transcript may hold — a dict from a scripted agent, a
    message object with no metadata — because the counter that calls it must
    not raise on an unfamiliar message.
    """
    if isinstance(message, dict):
        metadata = message.get("response_metadata") or {}
    else:
        metadata = getattr(message, "response_metadata", None) or {}
    return bool(isinstance(metadata, dict) and metadata.get(LIMIT_MARKER))


def _workdir(runtime):
    """The goal's workspace, or None when there is no context to read it from.

    None is a real case, not a defensive flourish: a test may drive the graph
    with no `context_schema`. The hooks below then do nothing, which leaves
    the agent exactly as it behaves with no limiter at all rather than
    failing on a missing attribute.
    """
    return getattr(getattr(runtime, "context", None), "workdir", None)


def limiter(limit):
    """The middleware instance. Built here so the import stays lazy."""
    from langchain.agents.middleware import AgentMiddleware, hook_config
    from langchain_core.messages import AIMessage

    class PersistentModelCallLimit(AgentMiddleware):
        """Ends the run once the goal has spent `limit` model calls in total.

        Total means across every pass. That is the entire difference between this
        and `ModelCallLimitMiddleware`, and it is the difference between a cap
        that holds and one that resets.

        `exit_behavior` is deliberately not offered. The verdict on this path is
        derived from the record, so ending leaves an honest partial result exactly
        as the wall-clock stop does, where raising would lose the goal to an
        exception. That was already the harness's choice; hard-coding it here
        keeps the one reasonable option from being configured into the other.
        """

        def __init__(self, limit):
            super().__init__()
            self.limit = int(limit)

        def _exceeded(self, runtime):
            workdir = _workdir(runtime)
            if not workdir or self.limit <= 0:
                return None
            spent = budget.model_calls(workdir)
            if spent < self.limit:
                return None
            return {
                "jump_to": "end",
                "messages": [AIMessage(
                    content=(
                        f"Model call limit reached: {spent}/{self.limit} for this "
                        f"goal, counted across every pass. Stopping."
                    ),
                    response_metadata={LIMIT_MARKER: True},
                )],
            }

        @hook_config(can_jump_to=["end"])
        def before_model(self, state, runtime):
            return self._exceeded(runtime)

        @hook_config(can_jump_to=["end"])
        async def abefore_model(self, state, runtime):
            return self._exceeded(runtime)

        # CHARGED AROUND THE CALL, not in `after_model`. MEASURED on the Aura
        # path: a spoke's final answer and its self-validation reply were
        # never counted (real 3 vs budget 1, real 4 vs budget 2), because
        # `SpokeSelfValidationMiddleware.after_model` jumps (back to the
        # model, or to the end) and a jump skips the `after_model` hooks
        # after it. `wrap_model_call` runs exactly once per real model
        # invocation whatever another hook decides; the limit notice, which
        # `before_model` produces without calling the model, never enters it.
        def _charge(self, runtime):
            workdir = _workdir(runtime)
            if workdir:
                budget.charge_model_call(workdir)

        def wrap_model_call(self, request, handler):
            response = handler(request)
            self._charge(request.runtime)
            return response

        async def awrap_model_call(self, request, handler):
            response = await handler(request)
            self._charge(request.runtime)
            return response

    return PersistentModelCallLimit(limit)
