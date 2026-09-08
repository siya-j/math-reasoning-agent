"""A stop that never tried the goal is refused, and the refusal is bounded.

MEASURED on `exercise_1_19` in eval/results/mixed-1.json. The agent wrote one
skeleton, proved a helper lemma, searched Mathlib twice for a name that does
not exist (`nsmul_eq_smul_cast`), got nothing both times, and then ended its
turn with the sentence "Wait, let's search for `nsmul_eq_smul_cast` in
Mathlib." repeated fifteen times and NO TOOL CALL. The graph ends when the
model returns no tool calls, so the run was recorded `not_proved` having spent
7 of 40 compiles, 0 attempts at the goal, and 71,316 output tokens on that
repetition. It never called `finish` either, which `TASK` instructs it to call
whatever happened -- so there is no judgement in the record that the goal was
too hard. The loop simply fell out from under a degenerate turn.

Every test here drives the REAL harness path with a real agent, real tools and
a real record, because a hand-built transcript fixture would pass against a
harness that had stopped continuing at all.
"""

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from math_v2 import harness
from math_v2.core import log

STATEMENT = "theorem mra_goal : 2 + 2 = 4"


def scripted(*turns):
    """A fake model that can request tools AND remembers what it was sent.

    `turns`: ("tool_name", {args}) for a tool call, or a str for prose. A
    prose turn has no tool calls, which is what ends the graph -- the exact
    shape of the measured failure.

    Returns (model, prompts). The recorder is a CLOSURE rather than an
    attribute because `GenericFakeChatModel` is a pydantic model and rejects
    assignment of fields it does not declare. `prompts` is how a test asks
    whether the harness really re-drove the agent, and with what.
    """
    messages = []
    for index, turn in enumerate(turns, start=1):
        if isinstance(turn, str):
            messages.append(AIMessage(content=turn))
        else:
            name, args = turn
            messages.append(AIMessage(content="", tool_calls=[
                {"name": name, "args": args, "id": f"call-{index}"}
            ]))
    # Padding, so a harness that drives more passes than a test expects fails
    # on that test's assertion rather than on StopIteration from the iterator.
    messages.extend(AIMessage(content="padding") for _ in range(8))

    prompts = []

    class RecordingFake(GenericFakeChatModel):
        # `bind_tools` raises NotImplementedError on the base class, so the
        # graph never reaches the tool node without this override.
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, sent, *args, **kwargs):
            prompts.append(list(sent))
            return super()._generate(sent, *args, **kwargs)

    return RecordingFake(messages=iter(messages)), prompts


@pytest.fixture
def compiler_rejects(monkeypatch):
    """Lean says no to everything. The point is never whether a proof lands."""
    from verifiers.lean_runner import LeanOutcome, LeanResult

    from math_v2.tools import proving as proving_tools

    async def no(source):
        return LeanResult(LeanOutcome.ERRORS, "3:2: error: unsolved goals")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda w: no)


# ------------------------------------------------------- THE regression
def test_a_stop_with_no_attempt_at_the_goal_is_refused(tmp_path, compiler_rejects):
    """`exercise_1_19` itself: a lemma, then prose, then silence.

    The assertion is on the RECORD -- a `log.PROOF` entry exists, so the goal
    was really attempted -- and not on how many times the model was called. A
    harness that re-drove the agent without the agent doing anything would
    satisfy a call count.
    """
    model, _ = scripted(
        ("try_lemma", {"statement": "lemma helper : 1 = 1", "proof": "by rfl"}),
        "Wait, let's search for `nsmul_eq_smul_cast` in Mathlib.",
        # Reachable ONLY on a continuation: the graph ended at the prose above.
        ("try_proof", {"proof": "by norm_num", "statement": STATEMENT}),
        "done",
    )

    harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    assert log.records(str(tmp_path), log.PROOF), (
        "the harness accepted a stop with zero attempts at the goal"
    )


def test_the_continuation_tells_the_model_what_the_record_says(tmp_path,
                                                              compiler_rejects):
    """The instruction is written by the harness FROM THE RECORD.

    Prose cannot be asked whether prose is degenerate, so the numbers in the
    message come from the budget file. This also pins that the transcript is
    re-sent rather than the agent restarted cold -- a cold restart would throw
    away the lemma it had already proved.
    """
    model, prompts = scripted(
        ("try_lemma", {"statement": "lemma helper : 1 = 1", "proof": "by rfl"}),
        "I will stop here.",
        "still stopping",
    )

    harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    continuations = [p for p in prompts
                     if any("STOP." in str(getattr(m, "content", ""))
                            for m in p)]
    assert continuations, "no continuation instruction ever reached the model"

    sent = str(continuations[0][-1].content)
    assert "compilations" in sent and "searches" in sent, sent
    assert "try_proof" in sent, sent

    # The transcript came too: the earlier turns are still there.
    assert len(continuations[0]) > 2, "the agent was restarted cold"


def test_an_agent_that_did_try_is_left_alone(tmp_path, compiler_rejects):
    """The line this guard must not cross.

    Whether stopping early with budget left is good judgement or timidity is
    an open question in this repo -- `Telemetry.lean_budget` exists to collect
    the evidence. A nudge that fired after a real attempt would answer it by
    assumption, and would spend forty compiles on goals the agent had read
    correctly. So the condition is the narrow one that was measured: ZERO
    attempts.
    """
    # BOTH proofs are non-generic on purpose. The first draft of this test
    # used `by norm_num` then `by simp`, and passed even against a harness
    # widened to prod every agent -- because `_generic_already_failed` refuses
    # a second bare closer WITHOUT compiling, so the second attempt could
    # never have left a record whatever the continuation did. The test was
    # vacuous, and vacuously green.
    model, _ = scripted(
        ("try_proof", {"proof": "by exact Nat.add_comm 2 2",
                       "statement": STATEMENT}),
        "That did not work and I have no other idea.",
        # If the harness prods anyway, this fires and a second record appears.
        ("try_proof", {"proof": "by linarith [Nat.zero_le 2]",
                       "statement": STATEMENT}),
        "done",
    )

    harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    assert len(log.records(str(tmp_path), log.PROOF)) == 1, (
        "the harness prodded an agent that had already attempted the goal"
    )


def test_a_skeleton_is_not_an_attempt_at_the_goal(tmp_path, compiler_rejects):
    """`exercise_1_19` had written one, and it is still zero attempts.

    A skeleton compiles the goal with `sorry` holes: it establishes that a
    plan is well formed and proves nothing. This is the same rule
    `eval.proof_metrics._AT_THE_GOAL` applies to the same records, and if the
    two ever disagree then the metric and the guard are counting different
    things.
    """
    model, _ = scripted(
        ("try_skeleton", {"proof": "by\n  have h : 1 = 1 := by rfl\n  sorry",
                          "statement": STATEMENT}),
        "I will stop here.",
        ("try_proof", {"proof": "by norm_num", "statement": STATEMENT}),
        "done",
    )

    harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    assert log.records(str(tmp_path), log.PROOF), (
        "a skeleton was accepted as an attempt at the goal, so the stop stood"
    )


# ------------------------------------------------------------- the bounds
def test_the_refusal_is_bounded(tmp_path, compiler_rejects):
    """A model that will not try after two instructions will not after ten,
    and every pass re-sends the whole transcript at full price."""
    model, prompts = scripted(*(["nope"] * 12))

    harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    assert not log.records(str(tmp_path), log.PROOF)
    assert len(prompts) <= harness.MAX_CONTINUATIONS + 1, (
        f"drove the agent {len(prompts)} times for one goal"
    )


def test_no_continuation_without_compiles_to_spend(tmp_path):
    """Below the floor a continuation cannot accomplish anything: a rejected
    attempt costs one compile and the repair it suggests costs another."""
    from math_v2.core import budget

    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.charge_lean(workdir, budget.MAX_LEAN_CALLS)

    assert harness._never_tried_the_goal(workdir) == 0, (
        "would have prodded the agent with no compilations left to use"
    )


def test_a_failing_continuation_does_not_cost_the_run_its_result(tmp_path,
                                                                 compiler_rejects):
    """The continuation is extra work the harness CHOSE to do, on top of a goal
    that had already finished. If it throws, the outcome is still the one the
    first pass earned.

    MEASURED as a real regression while building this: the fake models in
    `test_mathv2_async_bridge.py` carry a finite script, so the continuation
    exhausted the iterator and the whole goal came back `agent failed:
    RuntimeError` -- ERROR, excluded from every rate -- for a run whose first
    pass had recorded a perfectly good rejection. In a live run the same shape
    is a transient API fault on the extra call, and every goal in the run
    would have carried that risk.
    """
    calls = []

    class ExplodesAfterTheFirstPass:
        async def ainvoke(self, payload, context=None):
            calls.append(payload)
            if len(calls) > 1:
                raise RuntimeError("provider fell over on the extra call")
            from langchain.tools import ToolRuntime

            from math_v2.tools.proving import try_lemma

            await try_lemma.ainvoke({
                "statement": "lemma helper : 1 = 1", "proof": "by rfl",
                "runtime": ToolRuntime(
                    state=None, context=context, config={},
                    stream_writer=lambda *a, **k: None,
                    tool_call_id="t", store=None),
            })
            return {"messages": [type("M", (), {"text": "I stop here"})()]}

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=lambda *a, **k: ExplodesAfterTheFirstPass())

    assert len(calls) > 1, "the continuation never fired, so nothing was tested"
    assert not any(entry.startswith("agent failed") for entry in run.trace), (
        f"a failed continuation was recorded as a failed run: {run.trace}"
    )
    assert any("continuation failed" in entry for entry in run.trace), (
        f"the failure was swallowed without a trace: {run.trace}"
    )
    # The work the first pass did is still there.
    assert log.records(str(tmp_path), log.LEMMA), run.trace
