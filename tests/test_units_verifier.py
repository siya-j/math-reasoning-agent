"""The units verifier (Phase 1).

The tests are grouped by what they defend, not by which method they call.
The important group is the last one: what the verifier must REFUSE. A units
verifier that guesses is worse than none, because its verdicts read as
physical evidence.
"""

import pytest

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers.units_verifier import UnitsVerifier

VERIFIER = UnitsVerifier()


def decide(**kwargs) -> VerificationStatus:
    return VERIFIER.verify(VerificationRequest(**kwargs)).status


def dimension(lhs: str, rhs: str) -> VerificationStatus:
    return decide(kind=VerificationKind.DIMENSION, lhs=lhs, rhs=rhs)


def quantity(lhs: str, rhs: str, tolerance: str = "") -> VerificationStatus:
    return decide(
        kind=VerificationKind.QUANTITY, lhs=lhs, rhs=rhs, tolerance=tolerance
    )


# --------------------------------------------------------------- dimensions
@pytest.mark.parametrize(
    "lhs, rhs",
    [
        ("joule", "kilogram*meter**2/second**2"),
        ("newton", "kilogram*meter/second**2"),
        ("watt", "joule/second"),
        ("meter/second", "kilometer/hour"),
        ("pascal", "newton/meter**2"),
    ],
)
def test_the_same_dimension_written_two_ways_is_recognised(lhs, rhs):
    """SymPy calls a joule `energy` and kg*m**2/s**2 `length**2*mass/time**2`.

    Those are one dimension in two notations. Comparing the surface form
    would call them different and report a correct formula as wrong.
    """
    assert dimension(lhs, rhs) is VerificationStatus.TRUE


@pytest.mark.parametrize(
    "lhs, rhs",
    [
        ("joule", "newton"),
        ("meter", "second"),
        ("watt", "joule"),
        ("kilogram", "newton"),
    ],
)
def test_different_dimensions_are_refuted(lhs, rhs):
    assert dimension(lhs, rhs) is VerificationStatus.FALSE


def test_adding_unlike_quantities_is_not_a_quantity():
    """The classic units error. There is no value to rule on, but the claim
    that the sum is well defined is false, and silence would be worse."""
    assert dimension("5*meter + 3*second", "meter") is VerificationStatus.FALSE


# --------------------------------------------------------------- quantities
def test_a_correct_calculation_with_units_is_confirmed():
    assert quantity(
        "0.5*9.8*(meter/second**2)*(3*second)**2", "44.1*meter"
    ) is VerificationStatus.TRUE


def test_a_wrong_number_is_refuted():
    assert quantity(
        "0.5*9.8*(meter/second**2)*(3*second)**2", "29.4*meter"
    ) is VerificationStatus.FALSE


def test_the_right_number_in_the_wrong_unit_is_refuted():
    """THE reason this verifier exists.

    44.1 is the right number and `44.1 seconds` is a wrong answer. A verifier
    that sees only the number cannot tell these apart, and would confirm a
    distance stated as a time.
    """
    assert quantity(
        "0.5*9.8*(meter/second**2)*(3*second)**2", "44.1*second"
    ) is VerificationStatus.FALSE


def test_units_are_converted_before_comparing():
    """1000 metres and 1 kilometre are the same answer written differently."""
    assert quantity("1000*meter", "1*kilometer") is VerificationStatus.TRUE


def test_a_defined_conversion_is_confirmed():
    assert quantity("atmosphere", "101325*pascal") is VerificationStatus.TRUE
    assert quantity(
        "electronvolt", "1.602176634e-19*joule"
    ) is VerificationStatus.TRUE


def test_kinetic_energy_without_the_half_is_refuted():
    assert quantity(
        "0.5*2*kilogram*(3*meter/second)**2", "18*joule"
    ) is VerificationStatus.FALSE


# ------------------------------------------------------------------ rounding
def test_a_value_is_compared_at_the_stated_precision():
    """1*0.08206*273.15 is 22.4147, which is 22.415 to three places."""
    assert quantity(
        "1*0.08206*273.15*liter", "22.415*liter", tolerance="3"
    ) is VerificationStatus.TRUE


def test_rounding_does_not_excuse_a_wrong_digit():
    assert quantity(
        "1*0.08206*273.15*liter", "22.414*liter", tolerance="3"
    ) is VerificationStatus.FALSE


def test_without_a_stated_precision_the_comparison_is_strict():
    """Floating point noise is forgiven; a different answer is not."""
    assert quantity("22.4147*liter", "22.415*liter") is VerificationStatus.FALSE


# ------------------------------------------------------------- what it refuses
def test_a_claim_with_no_units_is_handed_back():
    """This is arithmetic. Deciding it here would report `dimensionally
    consistent` about something with no dimensions, which reads as physical
    evidence and is not."""
    assert dimension("2+2", "4") is VerificationStatus.UNKNOWN
    assert quantity("2+2", "4") is VerificationStatus.UNKNOWN


def test_nonsense_never_raises_and_never_confirms():
    for text in ("", "((", "__import__('os')", "meter +"):
        for kind in (VerificationKind.DIMENSION, VerificationKind.QUANTITY):
            status = decide(kind=kind, lhs=text, rhs="meter")
            assert status is not VerificationStatus.TRUE


def test_the_parser_has_no_access_to_the_interpreter():
    """The expression strings come from a language model."""
    status = decide(
        kind=VerificationKind.QUANTITY, lhs="__import__('os').system('echo x')",
        rhs="1*meter",
    )
    assert status is VerificationStatus.UNKNOWN


def test_it_declines_kinds_that_are_not_its_business():
    assert not VERIFIER.supports(
        VerificationRequest(kind=VerificationKind.PRIMALITY, lhs="7")
    )
    assert VERIFIER.supports(
        VerificationRequest(kind=VerificationKind.DIMENSION, lhs="meter", rhs="meter")
    )
