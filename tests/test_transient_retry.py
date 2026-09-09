"""A dropped connection must not cost the goal.

MEASURED. `exercise_2_11_22` has died with `agent failed: ReadError` in THREE
consecutive runs -- eval/results/mixed-1-rebuilt.json,
failures-after-decompose.json and proofnet-20-after-soundness.json. In the
last it had already spent 289 seconds and three compilations when the read
failed. `classify` returns ERROR, which is right -- a run that died is
evidence about nothing -- and the consequence is that the ProofNet denominator
has silently been 19 instead of 20 in every number this project has quoted.

A retry is sound here for a specific reason rather than by general optimism:
everything the agent did is already on disk, and `proof_state` re-derives the
whole picture from `math/proof_log.json` in one uncharged call. The retried
pass resumes with the record intact even though the transcript is gone.
"""

import asyncio

import pytest

from domain.proof import ProofRun
from math_v2 import harness
from math_v2.core import log


class ReadError(Exception):
    """The real one, by name. `_is_transient` matches on the type name so the
    provider stack need not be imported here -- and neither does this test
    need the provider installed to exercise the path."""


class Wiring(TypeError):
    """A bug, not a fault. Must NOT be retried."""


def _agent(failures, exc=ReadError, on_call=None):
    """An agent that raises `exc` the first `failures` times, then succeeds."""
    state = {"calls": 0}

    class Agent:
        async def ainvoke(self, payload, context=None):
            state["calls"] += 1
            if on_call:
                on_call(context)
            if state["calls"] <= failures:
                # `exc` may be a class or a factory taking the message, so a
                # test can supply a realistic provider message.
                raise exc("connection dropped")
            return {"messages": [type("M", (), {"text": "done"})()]}

    return Agent(), state


@pytest.fixture(autouse=True)
def _isolate_the_retry(monkeypatch):
    """No backoff, and no continuations.

    The backoff is real in production and pointless here. Continuations are
    switched off because they would otherwise be counted as retries: these
    fake agents return "done" without attempting the goal, so `_stopped_short`
    correctly drives two further passes and a call count of 2 becomes 4. That
    interaction is real and is covered on purpose by
    `test_a_retry_and_a_continuation_compose`, which turns them back on.
    """
    monkeypatch.setattr(harness, "TRANSIENT_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(harness, "MAX_CONTINUATIONS", 0)


# ------------------------------------------------------- THE regression
def test_a_transient_failure_is_retried(tmp_path):
    agent, state = _agent(failures=1)

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=lambda *a, **k: agent)

    assert state["calls"] == 2, "the dropped connection was not retried"
    assert not any(e.startswith("agent failed") for e in run.trace), run.trace
    assert any("retried after transient failure" in e for e in run.trace), (
        f"the retry left no record: {run.trace}"
    )


def test_the_retry_is_bounded(tmp_path):
    """A provider that is down stays down. Retrying forever would spend the
    wall clock discovering that."""
    agent, state = _agent(failures=99)

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=lambda *a, **k: agent)

    assert state["calls"] == harness.MAX_TRANSIENT_RETRIES + 1, state
    assert any(e.startswith("agent failed") for e in run.trace), (
        "a permanently failing provider must still be recorded as a failure"
    )


def test_a_wiring_fault_is_not_retried(tmp_path):
    """The line this must not cross. A TypeError is a bug, and retrying it
    turns a clear stack trace into a slow one."""
    agent, state = _agent(failures=99, exc=Wiring)

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=lambda *a, **k: agent)

    assert state["calls"] == 1, "a programming error was retried"


def test_a_wrapped_transient_is_still_transient(tmp_path):
    """Provider SDKs wrap. The ReadError that killed `exercise_2_11_22`
    arrives inside whatever the client raises, so matching only the outermost
    type would miss it."""
    class Wrapper(Exception):
        pass

    def raise_wrapped(_context):
        pass

    state = {"calls": 0}

    class Agent:
        async def ainvoke(self, payload, context=None):
            state["calls"] += 1
            if state["calls"] == 1:
                inner = ReadError("dropped")
                outer = Wrapper("provider error")
                outer.__cause__ = inner
                raise outer
            return {"messages": [type("M", (), {"text": "done"})()]}

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=lambda *a, **k: Agent())
    assert state["calls"] == 2, "a wrapped ReadError was not recognised"


# ------------------------------------------------- cost stays honest
def test_a_retried_run_does_not_claim_its_cost_was_measured(tmp_path):
    """The crashed pass's usage died with its transcript, so what comes back
    covers only the pass that succeeded. Reporting that as a measured total is
    exactly the "zero cost for work that certainly cost something" failure
    `Telemetry.complete` was added to prevent."""
    agent, _ = _agent(failures=1)

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=lambda *a, **k: agent)

    assert run.telemetry.complete is False, (
        "a retried run reported its cost as measured"
    )


def test_a_clean_run_still_reports_its_cost_as_measured(tmp_path):
    """The control. Marking every run incomplete would make the flag useless."""
    agent, _ = _agent(failures=0)

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=lambda *a, **k: agent)

    assert run.telemetry.complete is True


# ------------------------------------------- the record survives the crash
def test_the_retried_pass_sees_the_work_the_crashed_one_did(tmp_path):
    """WHY this is a resume and not a restart. The proof log is on disk, so
    whatever the crashed pass compiled is still there for the retry to build
    on -- the same property that makes clearing old tool results safe in this
    path."""
    workdir = str(tmp_path)
    seen = {}
    state = {"calls": 0}

    class Agent:
        async def ainvoke(self, payload, context=None):
            state["calls"] += 1
            if state["calls"] == 1:
                log.keep_lemma(workdir, "lemma survived : True := trivial")
                raise ReadError("dropped after real work")
            seen["lemmas"] = log.kept_lemmas(workdir)
            return {"messages": [type("M", (), {"text": "done"})()]}

    harness.prove("q", model=object(), workdir=workdir,
                  agent_factory=lambda *a, **k: Agent())

    assert seen.get("lemmas"), "the retried pass could not see the earlier work"
    assert "survived" in seen["lemmas"][0]


def test_a_retry_and_a_continuation_compose(tmp_path, monkeypatch):
    """The two mechanisms stack, and neither is bounded by the other.

    A crashed pass is retried; the retried pass returns without attempting the
    goal; the continuation guard then drives it again. Both bounds hold at
    once, so the worst case is
    `(MAX_TRANSIENT_RETRIES + 1) * (MAX_CONTINUATIONS + 1)` -- finite and
    known, which is the property that matters. This is the only test here with
    continuations enabled.
    """
    monkeypatch.setattr(harness, "MAX_CONTINUATIONS", 2)
    agent, state = _agent(failures=1)

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=lambda *a, **k: agent)

    ceiling = (harness.MAX_TRANSIENT_RETRIES + 1) * (harness.MAX_CONTINUATIONS + 1)
    assert state["calls"] <= ceiling, f"{state['calls']} passes for one goal"
    assert state["calls"] > 2, (
        "the continuation guard did not run after the retry; this test is "
        "no longer covering the interaction"
    )
    assert any("retried after transient failure" in e for e in run.trace)
    assert any("refused the stop" in e for e in run.trace), run.trace


# ==================================================================
# Exhaustion that waiting cannot fix must not be waited on
# ==================================================================
class ResourceExhausted(Exception):
    """The provider's class for BOTH a per-minute rate limit and a monthly
    spending cap. Identical type, identical 429 status."""


def test_a_spending_cap_is_not_retried(tmp_path):
    """MEASURED on eval/results/proofnet-60.json. A monthly billing cap
    arrives as `(RESOURCE_EXHAUSTED): 429`, the same class and status as an
    ordinary rate limit -- so the retry backed off twice per goal and spent
    132 seconds across three goals waiting for a wall that was not moving."""
    agent, state = _agent(
        failures=99,
        exc=lambda msg: ResourceExhausted(
            "429 RESOURCE_EXHAUSTED. Your project has exceeded its monthly "
            "spending cap. Please go to..."))

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=lambda *a, **k: agent)

    assert state["calls"] == 1, (
        f"waited {state['calls'] - 1} times for a billing cap to clear"
    )


def test_an_ordinary_rate_limit_is_still_retried(tmp_path):
    """The line this must not cross. A per-minute limit DOES clear, and it is
    the reason `ResourceExhausted` is in the transient set at all."""
    agent, state = _agent(
        failures=1,
        exc=lambda msg: ResourceExhausted(
            "429 RESOURCE_EXHAUSTED. Rate limit exceeded, retry shortly."))

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=lambda *a, **k: agent)

    assert state["calls"] == 2, "a transient rate limit was not retried"


def test_the_message_match_only_makes_failure_faster(tmp_path):
    """A substring match on someone else's wording is fragile, so it is used
    ONLY to skip a backoff -- never to decide an outcome. If the provider
    reworded it tomorrow, the behaviour degrades to a pointless retry rather
    than to a wrong answer."""
    agent, _ = _agent(
        failures=99,
        exc=lambda msg: ResourceExhausted("429 exceeded your quota"))

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=lambda *a, **k: agent)

    # Still an error, exactly as an un-matched exhaustion would be.
    assert any(e.startswith("agent failed") for e in run.trace), run.trace
