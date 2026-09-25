"""The only budget in this project that bounds what is billed.

Every other limit counts ACTIONS -- compiles, searches, tool calls, wall
clock. None counts model calls, and model calls are what the bill is for.

MEASURED over 226 goal-runs: model calls run at a median 1.3x the counted
tool calls and up to 5x, so a goal can sit inside every existing limit and
still make 83 model calls -- which `exercise_2_5_30` did, for 5,175,349
input tokens.

AND THE COST IS QUADRATIC, because this path re-sends the whole history on
every call. Fitted over the 70 goal-runs carrying both numbers:

    input_tokens ~ 809.5 * model_calls^2
    sanity: 83 calls -> 5,576,642 predicted against 5,175,349 actual

so halving the calls quarters the cost.

FORTY IS DERIVED, NOT CHOSEN:

                 n   median   p90   p95   p99   max
    PROVED     111        8    27    40    60    79
    failed      71       20    37    57    83    83

    cap 40 -> loses 4 of 111 proofs      <- p95 of proved
    cap 50 -> loses 4 of 111 proofs      strictly worse, 65 fewer calls saved

Estimated saving on the failed runs above it: 14.8M input tokens, 28% of
everything spent, for 3.6% of the proofs.

AND THEN IT DID NOT BOUND A RUN -- it bounded a PASS.

The cap was enforced by LangChain's `ModelCallLimitMiddleware` with
`thread_limit`, chosen precisely so a continuation would not reset it. It
reset anyway: `thread_limit` accumulates in graph state, and that state only
crosses an `ainvoke` boundary when a checkpointer and a stable `thread_id`
carry it. `harness._one_pass` supplies neither, so each of the
`MAX_CONTINUATIONS` re-drives began a fresh count and the true ceiling was
`(MAX_CONTINUATIONS + 1) *` the cap -- 120 model calls at 40, not 40.

MEASURED on eval/results/heldout-restart-20.json with the cap set to 20:

    continuations   model calls
    0 (six goals)   19, 21, 21, 21, 21, 21
    1-2 (four)      23, 28, 28, 29

THE TESTS BELOW ARE WHY IT SURVIVED. Every one of them inspected the
middleware OBJECT -- that a `thread_limit` was set, that `run_limit` was not.
All of them passed throughout. Not one drove a run and counted the calls, so
none could tell a limit that was configured from a limit that held. The
producer-side assertions are kept, because a constant that never reaches
`create_agent` is a real bug this repo has shipped three times; but they are
no longer the whole story, and the tests that matter now run `harness.prove`
and count.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from math_v2 import harness


def _installed(monkeypatch):
    """The middleware list that actually reaches `create_agent`."""
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    import langchain.agents as agents_module
    monkeypatch.setattr(agents_module, "create_agent", fake_create_agent)
    harness.build_agent(model=None, tools=[], system_prompt="x")
    return captured.get("middleware") or []


def _cap(middleware):
    for item in middleware:
        if "ModelCallLimit" in type(item).__name__:
            return item
    return None


def test_the_cap_is_forty():
    """p95 of the proved distribution. Fifty is dominated by it -- the same
    four proofs lost for less saving."""
    assert harness.MAX_MODEL_CALLS == 40


def test_it_can_be_turned_off_for_a_comparison(monkeypatch):
    """Every number on record was produced without this cap, so reproducing
    one has to be possible."""
    monkeypatch.setattr(harness, "MAX_MODEL_CALLS", 0)
    assert _cap(_installed(monkeypatch)) is None


def test_the_cap_is_recorded_in_the_results_file():
    """A run bounded at 40 model calls and one unbounded are not comparable,
    and nothing else in the record would say which -- the same reason the
    Lean backend and the trimming policy are recorded."""
    policy = harness.context_policy()
    assert policy["max_model_calls"] == harness.MAX_MODEL_CALLS


def test_trimming_still_installed_alongside_it(monkeypatch):
    """Two middlewares, two different jobs: trimming shrinks each call, the
    cap bounds how many there are. Losing either to a wiring mistake would
    be invisible."""
    names = [type(m).__name__ for m in _installed(monkeypatch)]
    assert any("ContextEditing" in n for n in names), names
    assert any("ModelCallLimit" in n for n in names), names


@pytest.mark.parametrize("calls,expected_millions", [(40, 1.3), (83, 5.6)])
def test_the_quadratic_fit_predicts_the_measured_cost(calls, expected_millions):
    """The fit is what justifies the cap being worth more than its call
    count. If it were linear, capping 83 to 40 would save 52%; quadratic it
    saves 77%."""
    predicted = 809.5 * calls * calls / 1e6
    assert abs(predicted - expected_millions) < 0.4, predicted


def test_the_cap_reaches_the_agent(monkeypatch):
    """Producer side. A constant that is set and never passed through is the
    exact shape of bug this repo has shipped three times.

    Kept, but no longer load-bearing: this assertion passed for the entire
    life of the bug above. `test_the_cap_survives_a_continuation` is the one
    that would have caught it.
    """
    cap = _cap(_installed(monkeypatch))
    assert cap is not None, "no model-call limit reached create_agent"
    assert cap.limit == harness.MAX_MODEL_CALLS


from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from math_v2 import call_limit
from math_v2.core import budget, log
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


STATEMENT = "theorem mra_goal : 2 + 2 = 4"


def scripted(*turns, padding=40):
    """A fake model that can request tools. See `test_harness_continuation`.

    The padding is deliberately long here: these tests are ABOUT how many
    times the model is called, so the script must never be the thing that
    stops the run. If the cap fails, the test must fail on its assertion
    rather than on a StopIteration the cap would have prevented.
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
    messages.extend(AIMessage(content="padding") for _ in range(padding))

    class Fake(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    return Fake(messages=iter(messages))


@pytest.fixture
def compiler_rejects(monkeypatch):
    """Lean says no to everything. No test here is about a proof landing."""
    from verifiers.lean_runner import LeanOutcome, LeanResult

    from math_v2.tools import proving as proving_tools

    async def no(source):
        return LeanResult(LeanOutcome.ERRORS, "3:2: error: unsolved goals")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda w: no)


@pytest.fixture
def cap(monkeypatch):
    """Set the cap. A function, because each test wants a different number."""
    def apply(limit):
        monkeypatch.setattr(harness, "MAX_MODEL_CALLS", limit)
        return limit
    return apply


# --------------------------------------------------------- THE regression
def test_the_cap_survives_a_continuation(tmp_path, compiler_rejects, cap):
    """The one that fails against the old middleware.

    The script ends its first pass with prose and NO attempt at the goal,
    which is exactly the condition `_stopped_short` continues on. So a second
    pass happens, and the question this test asks is whether that pass starts
    its count at zero.

    With the cap at 5 and the old `thread_limit`, the second pass was handed a
    fresh 5 and the run spent 6. The assertion is on the total.
    """
    limit = cap(5)
    model = scripted(
        # Pass 1: one call, prose, no tool call -- the graph ends.
        "I will stop here without trying the goal.",
        # Pass 2 onwards: burn calls on a tool that is not an attempt at the
        # goal, so nothing else in the harness decides to stop first.
        *[("search_mathlib", {"query": f"lemma_{i}"}) for i in range(20)],
    )

    run = harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    assert run.telemetry.model_calls <= limit, (
        f"the cap did not survive a continuation: "
        f"{run.telemetry.model_calls} model calls against a cap of {limit}"
    )


def test_the_count_is_charged_to_the_record_not_to_graph_state(
        tmp_path, compiler_rejects, cap):
    """WHY the cap survives: the total is in the budget file.

    This pins the mechanism rather than the symptom. If a later change moves
    the count back into the agent's own state, the test above could still pass
    on a run that happened not to continue; this one cannot.
    """
    cap(4)
    model = scripted(*[("search_mathlib", {"query": f"lemma_{i}"})
                       for i in range(20)])

    harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    assert budget.model_calls(str(tmp_path)) > 0, (
        "no model call was ever charged to the goal's budget record"
    )


def test_a_continuation_is_not_started_with_nothing_left_to_spend(
        tmp_path, compiler_rejects, cap):
    """An exhausted run is not re-driven just to be ended again.

    `_stopped_short` returns the COMPILE budget, and a run can have compiles
    left while having no model calls left. Re-driving then buys one
    `before_model` that jumps straight to the end: no attempt, and a synthetic
    turn appended to the record for nothing.
    """
    # ONE, not two: the first pass must exhaust the cap by itself. At a cap
    # of 2 the run has a call left after pass 1, so the continuation there is
    # legitimate and the test would be asserting against correct behaviour.
    cap(1)
    model = scripted("stopping immediately", "stopping again", "and again")

    harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    trace = log.read(str(tmp_path)).get("trace") or []
    refusals = [t for t in trace if "refused the stop" in t]
    assert not refusals, (
        f"a continuation was started with no model calls left: {refusals}"
    )


# ------------------------------------------- the counter and the limiter agree
def test_the_limit_notice_is_not_counted_as_a_model_call(tmp_path,
                                                         compiler_rejects, cap):
    """The off-by-one that first exposed the bug.

    The limiter ends a run by injecting one synthetic assistant turn. Counting
    it reported 21 against a cap of 20 on every single-pass goal in
    eval/results/heldout-restart-20.json. A cap that holds is worth nothing if
    the number printed beside it still says it did not.
    """
    limit = cap(3)
    model = scripted(*[("search_mathlib", {"query": f"lemma_{i}"})
                       for i in range(20)])

    run = harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    assert run.telemetry.model_calls == limit, (
        f"expected exactly {limit} model calls, got "
        f"{run.telemetry.model_calls} -- the limiter's own notice is being "
        f"counted as a call"
    )


def test_is_limit_notice_tolerates_every_shape_a_transcript_holds():
    """It is called on every message, so it must not raise on any of them."""
    from langchain_core.messages import HumanMessage

    notice = AIMessage(content="stopped",
                       response_metadata={call_limit.LIMIT_MARKER: True})
    assert call_limit.is_limit_notice(notice)

    for ordinary in (AIMessage(content="real turn"),
                     HumanMessage(content="the task"),
                     {"role": "assistant", "content": "scripted"},
                     {"role": "assistant", "response_metadata": None},
                     "a bare string"):
        assert not call_limit.is_limit_notice(ordinary), ordinary


def test_a_cap_of_zero_means_no_limit(tmp_path, compiler_rejects, cap):
    """0 disables the cap, which is what `build_agent` already documented.

    Without this, "the cap holds" could be satisfied by a limiter that stops
    every run at zero calls.
    """
    cap(0)
    model = scripted(("search_mathlib", {"query": "anything"}),
                     "done")

    run = harness.prove("is 2 + 2 = 4?", model=model, workdir=str(tmp_path))

    assert run.telemetry.model_calls > 0, "a cap of 0 stopped the run"
