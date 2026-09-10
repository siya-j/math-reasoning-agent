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


def test_the_cap_reaches_the_agent(monkeypatch):
    """Producer side. A constant that is set and never passed through is the
    exact shape of bug this repo has shipped three times."""
    cap = _cap(_installed(monkeypatch))
    assert cap is not None, "no model-call limit reached create_agent"
    assert cap.thread_limit == harness.MAX_MODEL_CALLS


def test_it_counts_across_continuations_not_per_pass(monkeypatch):
    """`thread_limit`, not `run_limit`. `_continuation_allowance` re-drives
    the agent, so a per-invocation limit resets on every continuation and
    bounds nothing -- which is the whole failure being fixed."""
    cap = _cap(_installed(monkeypatch))
    assert cap.thread_limit == harness.MAX_MODEL_CALLS
    assert getattr(cap, "run_limit", None) is None, (
        "a run_limit would reset on each continuation")


def test_reaching_the_cap_ends_rather_than_raises(monkeypatch):
    """The verdict is derived from the record, so ending leaves an honest
    partial result exactly as the wall-clock budget does. `error` would lose
    the goal to an exception and with it everything already proved."""
    cap = _cap(_installed(monkeypatch))
    assert cap.exit_behavior == "end"


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
