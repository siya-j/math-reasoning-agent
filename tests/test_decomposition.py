"""Offline tests for the Phase 5 decomposition step.

The rule under test: auxiliary claims are EVIDENCE, never proof. No number
of verified sub-claims may change the main verdict.
"""

from llm.decomposer import _Decomposition
from domain.verdict import VerificationStatus
from fakes import ABSTRACT, BROKEN, GOOD, WRONG, FakeModel, aux
import pipeline

TWO_TRUE_CASES = _Decomposition(
    subclaims=[
        aux("the case n = 1", kind="numeric", lhs="1", rhs="1"),
        aux("the case n = 2", kind="numeric", lhs="1 + 2", rhs="3"),
    ]
)

ONE_COUNTEREXAMPLE = _Decomposition(
    subclaims=[
        aux("the case n = 1", kind="numeric", lhs="1", rhs="1"),
        aux("the case n = 2", kind="numeric", lhs="1 + 2", rhs="99"),
    ]
)


def run(formalizations, decompositions=None):
    model = FakeModel(formalizations, decompositions=decompositions)
    return pipeline.run("some question", model=model)


# --------------------------------------------------------------- triggering
def test_verified_claim_skips_decomposition():
    state = run([GOOD], [TWO_TRUE_CASES])
    assert state.subclaims == []


def test_refuted_claim_skips_decomposition():
    """FALSE is a decided answer; no evidence gathering needed."""
    state = run([WRONG], [TWO_TRUE_CASES])
    assert state.subclaims == []


def test_undecidable_claim_triggers_decomposition():
    state = run([BROKEN, BROKEN, BROKEN], [TWO_TRUE_CASES])
    assert state.verdict.status is VerificationStatus.UNKNOWN
    assert len(state.subclaims) == 2


def test_abstract_claim_triggers_decomposition():
    state = run([ABSTRACT], [TWO_TRUE_CASES])
    assert state.verdict.status is VerificationStatus.NOT_APPLICABLE
    assert len(state.subclaims) == 2


# ------------------------------------------------------- evidence integrity
def test_supporting_evidence_never_upgrades_the_verdict():
    """The whole point: special cases are not a proof."""
    state = run([ABSTRACT], [TWO_TRUE_CASES])
    assert all(s.supports for s in state.subclaims)
    assert state.verdict.status is VerificationStatus.NOT_APPLICABLE
    assert not state.verdict.was_verified


def test_counterexample_is_recorded_as_refuting():
    state = run([ABSTRACT], [ONE_COUNTEREXAMPLE])
    assert [s.supports for s in state.subclaims] == [True, False]
    assert any(s.refutes for s in state.subclaims)


def test_no_proposals_is_handled_cleanly():
    state = run([ABSTRACT])  # fake returns an empty decomposition by default
    assert state.subclaims == []
    assert any("no checkable" in entry for entry in state.trace)


def test_subclaims_are_verified_by_the_real_verifier():
    state = run([ABSTRACT], [TWO_TRUE_CASES])
    assert all(s.verdict.method == "sympy" for s in state.subclaims)
