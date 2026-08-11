"""Offline tests for model-call retries. No waiting, no network.

The proving path had no backoff, so a transient limit ended a run that the
verification path — which has retried since Phase 4 — would have survived.
These tests pin what is retried and, more importantly, what is not.
"""

import pytest

from llm.retry import call_with_backoff


def failing(times, error, then="ok"):
    """Raises `error` the first `times` calls, then returns `then`."""
    state = {"n": 0}

    def call():
        state["n"] += 1
        if state["n"] <= times:
            raise error
        return then

    call.calls = lambda: state["n"]
    return call


def no_sleep(seconds):
    pass


# --------------------------------------------------------------- transient
def test_a_rate_limit_is_retried_and_can_succeed():
    call = failing(2, RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert call_with_backoff(call, sleep=no_sleep) == "ok"
    assert call.calls() == 3


def test_backoff_grows_between_attempts():
    waits = []
    call = failing(2, RuntimeError("quota exceeded"))
    call_with_backoff(call, backoff=10, sleep=waits.append)
    assert waits == [10, 20]


def test_retries_are_bounded():
    call = failing(99, RuntimeError("503 unavailable"))
    with pytest.raises(RuntimeError):
        call_with_backoff(call, attempts=3, sleep=no_sleep)
    assert call.calls() == 3


# --------------------------------------------------------------- permanent
def test_a_permanent_error_is_not_retried():
    """Retrying a malformed request wastes quota and cannot succeed."""
    call = failing(99, ValueError("model not found"))
    with pytest.raises(ValueError):
        call_with_backoff(call, sleep=no_sleep)
    assert call.calls() == 1, "a permanent error was retried"


# --------------------------------------------------------------- ambiguous
def test_invalid_argument_gets_exactly_one_retry():
    """Observed: Gemini returned INVALID_ARGUMENT for every call, including
    two-line prompts, straight after a long run — behaving like exhaustion
    rather than a bad request. One retry, not the full ladder."""
    call = failing(99, RuntimeError("INVALID_ARGUMENT"))
    with pytest.raises(RuntimeError):
        call_with_backoff(call, sleep=no_sleep)
    assert call.calls() == 2


def test_an_ambiguous_error_that_clears_is_recovered():
    call = failing(1, RuntimeError("INVALID_ARGUMENT"))
    assert call_with_backoff(call, sleep=no_sleep) == "ok"


# ------------------------------------------------------------- no failure
def test_a_successful_call_is_made_once():
    call = failing(0, RuntimeError("never raised"))
    assert call_with_backoff(call, sleep=no_sleep) == "ok"
    assert call.calls() == 1
