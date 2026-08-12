"""The harness must drive the agent through the ASYNC path.

Every math_v2 tool is an `async def`, so LangChain builds a `StructuredTool`
with a coroutine and no sync implementation. `agent.invoke()` therefore fails
the moment the model requests a tool:

    StructuredTool does not support sync invocation.

The failure happens inside the graph, so it presents as "the agent crashed"
with 0 model calls, 0 Lean calls and an empty record — which is exactly what
the first benchmark produced, before a single API call did any useful work.

`test_a_tool_calling_agent_runs_through_the_harness` is the regression guard.
It asserts the tool's EFFECT reached the record, not merely that no exception
was raised: a tool that silently did nothing would satisfy the weaker check.
"""

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from math_v2 import harness
from math_v2.core import log
from math_v2.tools import proving as proving_tools

STATEMENT = "theorem mra_goal : 2 + 2 = 4"
SYNC_ERROR = "does not support sync invocation"


class ToolCallingFake(GenericFakeChatModel):
    """A fake model that can actually request tools.

    `GenericFakeChatModel.bind_tools` raises NotImplementedError, so the graph
    never reaches the tool node without this. Everything else — the agent, the
    tools, the record — is real.
    """

    def bind_tools(self, tools, **kwargs):
        return self


def scripted_model(*tool_calls):
    messages = [
        AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": str(index)}
        ])
        for index, (name, args) in enumerate(tool_calls, start=1)
    ]
    messages.append(AIMessage(content="finished"))
    return ToolCallingFake(messages=iter(messages))


@pytest.fixture
def compiler_rejects(monkeypatch):
    from verifiers.lean_runner import LeanOutcome, LeanResult

    async def no(source):
        return LeanResult(LeanOutcome.ERRORS, "error: unsolved goals")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda w: no)


# --------------------------------------------------------------- the guard
def test_a_tool_calling_agent_runs_through_the_harness(tmp_path, compiler_rejects):
    """THE regression. A real agent, real tools, one real tool call."""
    run = harness.prove(
        "is 2 + 2 = 4?",
        model=scripted_model(("check_statement", {"statement": STATEMENT})),
        workdir=str(tmp_path),
    )

    assert not any(SYNC_ERROR in entry for entry in run.trace), run.trace
    assert not any("agent failed" in entry for entry in run.trace), run.trace

    # The tool really ran: it left a record. Absence of an error is not enough.
    assert log.records(str(tmp_path), log.STATEMENT_CHECK), (
        "no tool executed, so the async path was not actually exercised"
    )
    assert run.statement == STATEMENT


def test_several_tool_calls_in_one_run(tmp_path, compiler_rejects):
    run = harness.prove(
        "is 2 + 2 = 4?",
        model=scripted_model(
            ("check_statement", {"statement": STATEMENT}),
            ("try_proof", {"proof": "by norm_num"}),
            ("proof_state", {}),
        ),
        workdir=str(tmp_path),
    )

    assert not any(SYNC_ERROR in entry for entry in run.trace), run.trace
    assert len(log.records(str(tmp_path), log.PROOF)) == 1
    assert run.telemetry.lean_calls == 2      # the statement check, then the proof


def test_a_statement_check_is_reported_as_a_check_not_a_failed_proof(
        tmp_path, compiler_rejects):
    """A passing check reads as "compiles but uses sorry", which is not a failure.

    Rendered as a DIRECT attempt with an empty proof, it made an
    infrastructural failure look like a reasoning failure — and inflated
    `mean attempts`, which is meant to count tries at the goal.
    """
    run = harness.prove(
        "is 2 + 2 = 4?",
        model=scripted_model(("check_statement", {"statement": STATEMENT})),
        workdir=str(tmp_path),
    )

    assert run.attempts == [], "a statement check was counted as a proof attempt"
    assert any("statement check" in entry for entry in run.trace), run.trace


def test_the_model_calls_are_counted_rather_than_hardcoded(tmp_path,
                                                           compiler_rejects):
    run = harness.prove(
        "is 2 + 2 = 4?",
        model=scripted_model(("check_statement", {"statement": STATEMENT})),
        workdir=str(tmp_path),
    )
    assert run.telemetry.model_calls > 0, "reported 0 model calls for a real run"


# ------------------------------------------------------------- the bridge
def test_prove_stays_synchronous_for_the_evaluation_harness():
    """`scripts/evaluate_proofs.py` calls it synchronously and is unchanged."""
    import inspect

    assert not inspect.iscoroutinefunction(harness.prove)


def test_the_bridge_works_with_no_running_loop():
    async def answer():
        return "ok"

    assert harness._run_sync(answer()) == "ok"


def test_the_bridge_works_INSIDE_a_running_loop():
    """`asyncio.run` refuses to nest; a worker thread carries it instead."""

    async def outer():
        async def answer():
            return "nested"

        return harness._run_sync(answer())

    assert asyncio.run(outer()) == "nested"


def test_a_real_agent_run_survives_a_running_loop(tmp_path, compiler_rejects):
    async def outer():
        return harness.prove(
            "is 2 + 2 = 4?",
            model=scripted_model(("check_statement", {"statement": STATEMENT})),
            workdir=str(tmp_path),
        )

    run = asyncio.run(outer())
    assert not any(SYNC_ERROR in entry for entry in run.trace), run.trace


# ------------------------------------------------- entry-point selection
def test_the_async_entry_point_is_preferred_when_both_exist():
    """The real graph has both, and only `ainvoke` works with async tools."""
    used = []

    class Both:
        def invoke(self, payload, context=None):
            used.append("invoke")
            return {"messages": []}

        async def ainvoke(self, payload, context=None):
            used.append("ainvoke")
            return {"messages": []}

    harness.prove("q", model=object(), workdir=None,
                  agent_factory=lambda m, t, p: Both())
    assert used == ["ainvoke"]


def test_a_synchronous_scripted_agent_is_still_supported():
    """The existing tests inject sync fakes; they must keep working."""
    called = []

    class SyncOnly:
        def invoke(self, payload, context=None):
            called.append(payload)
            return {"messages": []}

    run = harness.prove("q", model=object(), agent_factory=lambda m, t, p: SyncOnly())
    assert called and run.verdict is not None


def test_an_agent_that_does_not_take_context_is_still_called():
    seen = []

    class NoContext:
        async def ainvoke(self, payload):
            seen.append(payload)
            return {"messages": []}

    harness.prove("q", model=object(), agent_factory=lambda m, t, p: NoContext())
    assert seen, "an agent without a context parameter was never invoked"


def test_context_support_is_inspected_rather_than_caught(tmp_path):
    """A try/except TypeError would swallow a TypeError raised INSIDE the agent.

    That is the same shape of bug as the one this file exists for: a real
    failure disguised as a fallback.
    """
    class Exploding:
        async def ainvoke(self, payload, context=None):
            raise TypeError("a real bug inside the agent")

    run = harness.prove("q", model=object(),
                        workdir=str(tmp_path),
                        agent_factory=lambda m, t, p: Exploding())

    assert any("a real bug inside the agent" in entry for entry in run.trace), (
        "a genuine TypeError was hidden by a retry"
    )


def test_takes_context_reads_a_signature():
    assert harness._takes_context(lambda payload, context=None: None)
    assert not harness._takes_context(lambda payload: None)
    assert harness._takes_context(lambda payload, **kwargs: None)
