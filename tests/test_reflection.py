"""Offline tests for the Phase 4 reflection loop.

The MODEL is faked; the VERIFIER is real. So these tests exercise the actual
retry policy against actual SymPy behaviour, with no API key and no network.
"""

from types import SimpleNamespace

import pytest

import config
import pipeline
from domain.attempt import Strategy
from domain.verdict import VerificationStatus
from llm.formalizer import _FormalizedCheck
from llm.interpreter import _InterpretedClaim
from pipeline.reflection import next_strategy, should_retry
from domain.verdict import Verdict

# Formal checks with known verifier outcomes.
GOOD = _FormalizedCheck(kind="equality", lhs="diff(x**3, x)", rhs="3*x**2")
WRONG = _FormalizedCheck(kind="numeric", lhs="2 + 2", rhs="5")
BROKEN = _FormalizedCheck(kind="equality", lhs="))((", rhs="1")
ABSTRACT = _FormalizedCheck(kind="none")

CLAIM = _InterpretedClaim(statement="a claim", problem_type="calculus", numbers=[])


class FakeModel:
    """Returns queued structured objects; plain .invoke() returns filler text."""

    def __init__(self, formalizations, interpretations=None):
        self.formalizations = list(formalizations)
        self.interpretations = list(interpretations or [])
        self.structured_calls = []

    def with_structured_output(self, schema):
        return _FakeStructured(self, schema)

    def invoke(self, prompt):
        return SimpleNamespace(text="filler text")

    def _next(self, schema):
        self.structured_calls.append(schema.__name__)
        if schema.__name__ == "_InterpretedClaim":
            return self.interpretations.pop(0) if self.interpretations else CLAIM
        return self.formalizations.pop(0)


class _FakeStructured:
    def __init__(self, parent, schema):
        self.parent, self.schema = parent, schema

    def invoke(self, prompt):
        return self.parent._next(self.schema)


def run(formalizations, interpretations=None):
    model = FakeModel(formalizations, interpretations)
    return pipeline.run("some question", model=model)


# ------------------------------------------------------------ retry policy
def test_true_verdict_does_not_retry():
    assert not should_retry(Verdict(VerificationStatus.TRUE, "sympy", ""))


def test_false_verdict_does_not_retry():
    """The critical one: a false claim is a correct answer, not a failure."""
    assert not should_retry(Verdict(VerificationStatus.FALSE, "sympy", ""))


def test_not_applicable_does_not_retry():
    assert not should_retry(Verdict(VerificationStatus.NOT_APPLICABLE, "none", ""))


def test_unknown_verdict_retries():
    assert should_retry(Verdict(VerificationStatus.UNKNOWN, "sympy", ""))


def test_strategy_escalates():
    assert next_strategy(2) is Strategy.REFORMALIZE
    assert next_strategy(3) is Strategy.REINTERPRET


# ------------------------------------------------------------------- loop
def test_success_first_try_makes_one_attempt():
    state = run([GOOD])
    assert len(state.attempts) == 1
    assert state.verdict.status is VerificationStatus.TRUE
    assert state.attempts[0].strategy is Strategy.INITIAL


def test_false_claim_is_never_retried():
    """Guards against the agreement machine: FALSE must terminate."""
    state = run([WRONG])
    assert len(state.attempts) == 1
    assert state.verdict.status is VerificationStatus.FALSE


def test_unknown_triggers_reformalization_then_succeeds():
    state = run([BROKEN, GOOD])
    assert len(state.attempts) == 2
    assert state.attempts[1].strategy is Strategy.REFORMALIZE
    assert state.verdict.status is VerificationStatus.TRUE


def test_second_failure_escalates_to_reinterpretation():
    state = run([BROKEN, BROKEN, GOOD])
    assert len(state.attempts) == 3
    assert [a.strategy for a in state.attempts] == [
        Strategy.INITIAL,
        Strategy.REFORMALIZE,
        Strategy.REINTERPRET,
    ]
    assert state.verdict.status is VerificationStatus.TRUE


def test_gives_up_honestly_after_max_attempts():
    state = run([BROKEN, BROKEN, BROKEN])
    assert len(state.attempts) == config.MAX_ATTEMPTS
    assert state.verdict.status is VerificationStatus.UNKNOWN
    assert not state.verdict.was_verified


def test_unverifiable_claim_is_not_retried():
    state = run([ABSTRACT])
    assert len(state.attempts) == 1
    assert state.verdict.status is VerificationStatus.NOT_APPLICABLE


def test_every_attempt_is_kept_in_memory():
    state = run([BROKEN, BROKEN, GOOD])
    assert [a.number for a in state.attempts] == [1, 2, 3]
    assert any("reflect" in entry for entry in state.trace)
