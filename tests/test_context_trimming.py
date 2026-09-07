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

    assert not (captured.get("middleware") or [])


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
