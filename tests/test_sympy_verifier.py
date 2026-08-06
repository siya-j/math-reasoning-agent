"""Offline tests for the deterministic half. No API key, no network."""

import pytest

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers import verify


def req(kind, lhs="", rhs="", variable="x", candidate=""):
    return VerificationRequest(
        kind=kind, lhs=lhs, rhs=rhs, variable=variable, candidate=candidate
    )


# ------------------------------------------------------------------ equality
def test_correct_derivative_is_true():
    v = verify(req(VerificationKind.EQUALITY, "diff(x**3, x)", "3*x**2"))
    assert v.status is VerificationStatus.TRUE


def test_wrong_derivative_is_false():
    v = verify(req(VerificationKind.EQUALITY, "diff(x**3, x)", "2*x"))
    assert v.status is VerificationStatus.FALSE


def test_pythagorean_identity_is_true():
    v = verify(req(VerificationKind.EQUALITY, "sin(x)**2 + cos(x)**2", "1"))
    assert v.status is VerificationStatus.TRUE


def test_integral_is_true():
    v = verify(req(VerificationKind.EQUALITY, "integrate(2*x, x)", "x**2"))
    assert v.status is VerificationStatus.TRUE


# ------------------------------------------------------------------- numeric
def test_correct_arithmetic_is_true():
    v = verify(req(VerificationKind.NUMERIC, "2 + 2", "4"))
    assert v.status is VerificationStatus.TRUE


def test_wrong_arithmetic_is_false():
    v = verify(req(VerificationKind.NUMERIC, "2 + 2", "5"))
    assert v.status is VerificationStatus.FALSE


# ----------------------------------------------------------------- primality
def test_prime_is_true():
    v = verify(req(VerificationKind.PRIMALITY, "7919"))
    assert v.status is VerificationStatus.TRUE


def test_composite_is_false_and_shows_factors():
    v = verify(req(VerificationKind.PRIMALITY, "7917"))
    assert v.status is VerificationStatus.FALSE
    assert "3" in v.detail


# ------------------------------------------------------------------ solution
def test_correct_solutions_are_true():
    v = verify(req(VerificationKind.SOLUTION, "x**2", "4", candidate="2, -2"))
    assert v.status is VerificationStatus.TRUE


def test_incomplete_solutions_are_false():
    v = verify(req(VerificationKind.SOLUTION, "x**2", "4", candidate="2"))
    assert v.status is VerificationStatus.FALSE


# ------------------------------------------------ honesty about what it can't do
def test_abstract_claim_is_not_applicable():
    v = verify(req(VerificationKind.NONE))
    assert v.status is VerificationStatus.NOT_APPLICABLE
    assert not v.was_verified


def test_garbage_expression_returns_unknown_not_a_crash():
    v = verify(req(VerificationKind.EQUALITY, "))((", "1"))
    assert v.status is VerificationStatus.UNKNOWN


# ------------------------------------------------------------------ security
def test_parser_refuses_python_builtins():
    v = verify(req(VerificationKind.EQUALITY, "__import__('os').getcwd()", "1"))
    assert v.status is VerificationStatus.UNKNOWN
    assert not v.was_verified
