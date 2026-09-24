"""Float representation error is not disagreement (Phase 5).

Found by the science reachability check, in code that predates it: asked
whether 0.7**2 + 2*0.7*0.3 + 0.3**2 equals 1, the numeric check reported
FALSE. The difference is -1.11e-16 — the cost of writing the inputs in
decimal, not a claim about arithmetic.

It rarely fired in mathematics, where inputs are usually exact integers and
rationals. In science, decimal inputs are the normal case.

The forgiveness is deliberately narrow, and these tests are mostly about its
edges, because a numeric check that forgives too much is worse than one that
forgives nothing.
"""

import pytest

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers.sympy_verifier import SymPyVerifier

VERIFIER = SymPyVerifier()


def numeric(lhs: str, rhs: str, tolerance: str = "") -> VerificationStatus:
    return VERIFIER.verify(
        VerificationRequest(
            kind=VerificationKind.NUMERIC, lhs=lhs, rhs=rhs, tolerance=tolerance
        )
    ).status


# --------------------------------------------------------------- forgiveness
@pytest.mark.parametrize(
    "lhs, rhs",
    [
        ("0.7**2 + 2*0.7*0.3 + 0.3**2", "1"),
        ("0.1 + 0.2", "0.3"),
        ("-log(0.001)/log(10)", "3"),
        ("1.1 * 3", "3.3"),
    ],
)
def test_representation_error_is_not_treated_as_disagreement(lhs, rhs):
    assert numeric(lhs, rhs) is VerificationStatus.TRUE


# ------------------------------------------------------------- what is strict
def test_exact_arithmetic_is_still_compared_exactly():
    """Nothing is forgiven where no decimal was written. The oldest test in
    the project still has to pass."""
    assert numeric("2+2", "5") is VerificationStatus.FALSE
    assert numeric("2+2", "4") is VerificationStatus.TRUE
    assert numeric("1/3", "1/3") is VerificationStatus.TRUE


@pytest.mark.parametrize(
    "lhs, rhs",
    [
        ("22.4146889", "22.414"),      # a genuinely different value
        ("1/3", "0.333"),              # a rounded value, not a rounding artefact
        ("0.1 + 0.2", "0.31"),
        ("9.8 * 3", "29.5"),
    ],
)
def test_a_real_difference_is_still_refuted(lhs, rhs):
    assert numeric(lhs, rhs) is VerificationStatus.FALSE


def test_a_small_quantity_is_not_confused_with_zero():
    """THE hole this nearly had. An absolute tolerance floor would measure
    the residue against 1 rather than against the quantity, and confirm that
    a photon energy of 3.3e-19 J equals nothing at all."""
    assert numeric("6.626e-34 * 5e14", "0") is VerificationStatus.FALSE
    assert numeric("1e-20", "0") is VerificationStatus.FALSE
    assert numeric("1e-20", "2e-20") is VerificationStatus.FALSE


def test_the_tolerance_is_relative_so_large_numbers_are_not_forgiven_more():
    assert numeric("6.02214076e23", "6.023e23") is VerificationStatus.FALSE
    assert numeric("6.02214076e23", "6.02214076e23") is VerificationStatus.TRUE


# ------------------------------------------------------------------ rounding
def test_a_question_that_states_its_rounding_is_judged_on_the_rounded_value():
    """1*0.08206*273.15 is 22.4146889, which is 22.415 to three places."""
    assert numeric("1*0.08206*273.15", "22.415", tolerance="3") is (
        VerificationStatus.TRUE
    )


def test_rounding_does_not_excuse_a_wrong_digit():
    assert numeric("1*0.08206*273.15", "22.414", tolerance="3") is (
        VerificationStatus.FALSE
    )


def test_an_unreadable_tolerance_is_ignored_rather_than_obeyed():
    assert numeric("2+2", "4", tolerance="lots") is VerificationStatus.TRUE
    assert numeric("2+2", "5", tolerance="lots") is VerificationStatus.FALSE


def test_the_verdict_says_when_it_forgave_something():
    """A reader must be able to tell a forgiven comparison from an exact
    one, or the detail string is hiding the thing it exists to show."""
    verdict = VERIFIER.verify(
        VerificationRequest(
            kind=VerificationKind.NUMERIC, lhs="0.1 + 0.2", rhs="0.3"
        )
    )
    assert verdict.status is VerificationStatus.TRUE
    assert "floating-point" in verdict.detail
