"""Old tool results are cleared once the input grows, and it is recorded.

WHY THIS EXISTS
---------------
MEASURED on eval/results/putnam-run4.json: 3,562,273 input tokens over five
goals -- about 29,700 per model call against a system prompt of roughly 4,000.
So ~85% of every call was conversation history re-sent, which is also why a
hard goal cost 16x the input of an easy one rather than 4x. `harness.py` says
why plainly: "There is no summarisation [...] middleware in this path."

THE LIBRARY DEFAULT WOULD HAVE BEEN A NO-OP. `ClearToolUsesEdit.trigger`
defaults to 100_000 and this workload peaks near 55_000, so installing the
middleware without moving that number would have changed nothing while
looking like a fix. Projected against run4's growth curve, 24_000 saves ~32%
and 16_000 ~50%; 24_000 is the default because clearing then begins only
after roughly nine model calls, leaving short goals untouched.

WHY CLEARING IS SAFE HERE, and it is not a general claim: this agent keeps its
state on disk and `proof_state` re-derives the whole picture from
`math/proof_log.json` in one uncharged call, so a cleared result is
recoverable by asking. An agent whose only memory is its transcript could not
do this.
"""

import pytest

from math_v2 import harness


def test_the_trigger_is_below_the_measured_peak_or_it_never_fires():
    """THE mistake this test exists to prevent. 100_000 is the library
    default and this workload peaks near 55_000 -- a trigger above the peak
    is a no-op that reads as a fix."""
    measured_peak = 55_371

    assert 0 < harness.CONTEXT_TRIM_TRIGGER < measured_peak, (
        f"trigger {harness.CONTEXT_TRIM_TRIGGER} cannot fire on a workload "
        f"peaking at {measured_peak}"
    )


def test_the_trigger_leaves_room_for_the_prompt_and_a_few_results():
    """Too low is its own failure: the system prompt alone is ~4,000 tokens,
    and a trigger near that would clear continuously and starve the model of
    the goal state it is working from."""
    system_prompt_tokens = 4_000

    assert harness.CONTEXT_TRIM_TRIGGER > 4 * system_prompt_tokens


def test_the_most_recent_results_are_always_kept():
    """The goal state the model is acting on is in the last result or two.
    `keep` is what makes trimming safe rather than merely cheap."""
    assert harness.CONTEXT_TRIM_KEEP >= 2


def test_the_orientation_tools_are_never_cleared():
    """`proof_state` is the way back to orientation and `check_statement` is
    where the model learned whether its own statement elaborated. Both are
    called rarely, so excluding them costs almost nothing."""
    assert "proof_state" in harness.CONTEXT_TRIM_KEEP_TOOLS
    assert "check_statement" in harness.CONTEXT_TRIM_KEEP_TOOLS


def test_the_agent_actually_gets_the_middleware(monkeypatch):
    """Not "is it configured" but "does it reach `create_agent`" -- the
    producer-side check, which is the half that has gone missing three times
    in this project."""
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    import langchain.agents as agents_module
    monkeypatch.setattr(agents_module, "create_agent", fake_create_agent)

    harness.build_agent(model=None, tools=[], system_prompt="x")

    installed = captured.get("middleware") or []
    assert installed, "no middleware reached create_agent"
    assert any("ContextEditing" in type(m).__name__ for m in installed), (
        [type(m).__name__ for m in installed]
    )


def test_trimming_can_be_turned_off_for_a_comparison(monkeypatch):
    """A benchmark that wants the old behaviour must be able to ask for it,
    or the historical numbers become unreproducible."""
    monkeypatch.setattr(harness, "CONTEXT_TRIM_TRIGGER", 0)
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    import langchain.agents as agents_module
    monkeypatch.setattr(agents_module, "create_agent", fake_create_agent)

    harness.build_agent(model=None, tools=[], system_prompt="x")

    # NO TRIMMING MIDDLEWARE -- not "no middleware at all". This asserted the
    # list was empty, which was true when trimming was the only entry and
    # became wrong the moment `ModelCallLimitMiddleware` was added. The
    # invariant is about TRIMMING being switchable; the model-call cap is a
    # separate budget with its own switch and its own test.
    installed = [type(m).__name__ for m in (captured.get("middleware") or [])]
    assert not any("ContextEditing" in name for name in installed), installed


def test_the_policy_is_recorded_in_the_environment(monkeypatch):
    """A run with trimming and one without are not comparable, and nothing
    else in the record would say which this was -- the same reason the Lean
    backend is recorded."""
    import config
    from pipeline.proving import environment

    monkeypatch.setattr(config, "PROVER", "math_v2")
    recorded = environment()

    assert recorded["context_trimming"] is True
    assert recorded["context_trim_trigger"] == harness.CONTEXT_TRIM_TRIGGER
    # and the backend is still there
    assert "lean_backend" in recorded


# ------------------------------------- trimming the half that is actually big
#
# MEASURED over 81 surviving workdirs, split by what `ClearToolUsesEdit` can
# and cannot touch:
#
#     tool inputs  (statement + proof)   700,997 chars  ~175k tokens
#     tool results (Lean's reply)        192,526 chars   ~48k tokens
#
# With `clear_tool_inputs=False` only the results went, so trimming could
# reclaim at most 22% of the tool traffic. That is why trigger=24,000 still
# produced 69,805 input tokens PER CALL on `exercise_4_5_22` and 174,637 on
# `exercise_3_22` -- 2.9x and 7.3x the threshold -- and why cost on this
# project's own data is quadratic in model calls (correlation 0.966 against
# calls^2, 0.933 against calls).
#
# For this agent the tool ARGUMENTS are the proofs. Clearing results while
# keeping arguments trims the small half.
def test_the_model_s_own_submissions_are_cleared_too():
    """The default has to be the one that reclaims the large half."""
    assert harness.CONTEXT_TRIM_INPUTS is True


def test_clearing_inputs_reaches_the_middleware(monkeypatch):
    """Producer-side again. A constant that is set and never passed through
    is the exact shape of bug this project has shipped three times."""
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return object()

    import langchain.agents as agents_module
    monkeypatch.setattr(agents_module, "create_agent", fake_create_agent)

    harness.build_agent(model=None, tools=[], system_prompt="x")

    edit = captured["middleware"][0].edits[0]
    assert edit.clear_tool_inputs is True, (
        "the arguments survive, so trimming reclaims only the 22% that is "
        "Lean's replies")


def test_clearing_the_submissions_is_recoverable_not_amnesia():
    """THE SAFETY ARGUMENT, and the thing that would silently invalidate it.

    Dropping the submissions from context is only acceptable because
    `proof_state` reports "the attempts the compiler rejected and why",
    compiles nothing, and is excluded from clearing. If that tool ever stops
    reporting them, clearing inputs becomes real forgetting while every
    other test here still passes.
    """
    from pathlib import Path

    assert "proof_state" in harness.CONTEXT_TRIM_KEEP_TOOLS
    source = (Path(harness.__file__).parent / "tools" / "proving.py").read_text(
        encoding="utf-8")
    body = source[source.index("async def proof_state"):]
    body = body[:body.index("async def", 20)]
    assert "rejected" in body, "proof_state no longer offers the way back"


def test_clearing_inputs_can_be_turned_off_for_a_comparison(monkeypatch):
    """The SAVING is unverified -- measuring it needs a paid run, and a run
    with this on is not comparable to one without."""
    import importlib

    monkeypatch.setenv("MRA_CONTEXT_TRIM_INPUTS", "0")
    reloaded = importlib.reload(harness)
    try:
        assert reloaded.CONTEXT_TRIM_INPUTS is False
    finally:
        monkeypatch.delenv("MRA_CONTEXT_TRIM_INPUTS", raising=False)
        importlib.reload(harness)


def test_the_input_policy_is_recorded_too():
    """Two runs differing only in this are not comparable, so the record has
    to name it."""
    policy = harness.context_policy()
    assert policy["context_trim_inputs"] is harness.CONTEXT_TRIM_INPUTS

