"""Molar masses and physical constants (Phase 2).

The theme running through these is PRECISION. A claim is judged at the
precision it was written to, because that is how a scientist grades one, and
because the alternative fails in both directions: strict digit comparison
calls 6.022e23 wrong, and a loose tolerance calls 6.023e23 right.
"""

import pytest

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from science.elements import FormulaError, molar_mass, parse_formula
from verifiers.reference_verifier import ReferenceVerifier, significant_figures

VERIFIER = ReferenceVerifier()


def mass(formula: str, claimed: str) -> VerificationStatus:
    return VERIFIER.verify(
        VerificationRequest(
            kind=VerificationKind.MOLAR_MASS, lhs=formula, rhs=claimed
        )
    ).status


def constant(name: str, claimed: str) -> VerificationStatus:
    return VERIFIER.verify(
        VerificationRequest(kind=VerificationKind.CONSTANT, lhs=name, rhs=claimed)
    ).status


# ----------------------------------------------------------- formula parsing
@pytest.mark.parametrize(
    "formula, counts",
    [
        ("H2O", {"H": 2, "O": 1}),
        ("CO2", {"C": 1, "O": 2}),
        ("C6H12O6", {"C": 6, "H": 12, "O": 6}),
        ("Ca(OH)2", {"Ca": 1, "O": 2, "H": 2}),
        ("Fe2(SO4)3", {"Fe": 2, "S": 3, "O": 12}),
        ("CuSO4.5H2O", {"Cu": 1, "S": 1, "O": 9, "H": 10}),
    ],
)
def test_formulae_are_counted_correctly(formula, counts):
    assert parse_formula(formula) == counts


def test_element_symbols_are_case_sensitive():
    """CO is carbon monoxide and Co is cobalt. A parser that ignores case
    answers a different question and gives no sign that it did."""
    assert parse_formula("CO") == {"C": 1, "O": 1}
    assert parse_formula("Co") == {"Co": 1}
    assert molar_mass("CO")[0] != pytest.approx(molar_mass("Co")[0])


@pytest.mark.parametrize("bad", ["", "Xx2", "H2O)", "(H2O", "123", "  "])
def test_an_unreadable_formula_is_refused_not_guessed(bad):
    """A wrong molar mass looks exactly like a right one."""
    with pytest.raises(FormulaError):
        parse_formula(bad)


def test_the_working_is_reported_with_the_answer():
    """A molar mass with no working is an assertion; with its terms it is
    evidence a human can audit without trusting this code."""
    total, working = molar_mass("H2O")
    assert total == pytest.approx(18.015)
    assert "1.008" in working and "15.999" in working


# ------------------------------------------------------------- molar masses
def test_a_molar_mass_is_confirmed_at_the_stated_precision():
    for claimed in ("18.015", "18.02", "18.0", "18"):
        assert mass("H2O", claimed) is VerificationStatus.TRUE


def test_rounding_does_not_excuse_a_wrong_digit():
    assert mass("H2O", "18.01") is VerificationStatus.FALSE
    assert mass("H2O", "20.0") is VerificationStatus.FALSE


def test_an_unreadable_formula_yields_no_verdict():
    assert mass("Xx9", "10") is VerificationStatus.UNKNOWN


def test_an_expression_is_not_a_quoted_value():
    """A value here should be quoted, not computed. An expression means the
    model is doing arithmetic where it was asked to look something up."""
    assert mass("H2O", "18.0*2") is VerificationStatus.UNKNOWN


# ---------------------------------------------------------------- constants
def test_significant_figures_are_counted_as_written():
    assert significant_figures("6.022e23") == 4
    assert significant_figures("0.001") == 1
    assert significant_figures("18.0") == 3
    assert significant_figures("9.80665") == 6


def test_a_rounded_constant_is_accepted_at_its_own_precision():
    assert constant("N_A", "6.022e23") is VerificationStatus.TRUE
    assert constant("c", "3e8") is VerificationStatus.TRUE
    assert constant("g", "9.8") is VerificationStatus.TRUE
    assert constant("R", "8.314") is VerificationStatus.TRUE


def test_a_wrong_digit_is_still_wrong():
    assert constant("N_A", "6.023e23") is VerificationStatus.FALSE
    assert constant("g", "9.9") is VerificationStatus.FALSE


def test_capital_G_and_lowercase_g_are_different_constants():
    """Eleven orders of magnitude apart, differing only in case. A lookup
    that lowercases collapses them, and collapses them silently."""
    assert constant("G", "6.6743e-11") is VerificationStatus.TRUE
    assert constant("g", "9.80665") is VerificationStatus.TRUE
    assert constant("G", "9.81") is VerificationStatus.FALSE


def test_a_measured_constant_is_not_decided_beyond_its_measurement():
    """FALSE here would assert knowledge that nobody has."""
    assert constant("G", "6.674300000e-11") is VerificationStatus.UNKNOWN


def test_an_exact_constant_can_be_decided_to_any_precision():
    assert constant("c", "299792458") is VerificationStatus.TRUE
    assert constant("c", "300000000") is VerificationStatus.FALSE


def test_a_constant_not_in_the_table_gets_no_verdict():
    assert constant("fudge factor", "1") is VerificationStatus.UNKNOWN


def test_it_declines_kinds_that_are_not_its_business():
    assert not VERIFIER.supports(
        VerificationRequest(kind=VerificationKind.PRIMALITY, lhs="7")
    )
    assert VERIFIER.supports(
        VerificationRequest(kind=VerificationKind.MOLAR_MASS, lhs="H2O")
    )


def test_nothing_raises_and_nothing_guesses():
    for kind in (VerificationKind.MOLAR_MASS, VerificationKind.CONSTANT):
        for text in ("", "((", "__import__('os')"):
            status = VERIFIER.verify(
                VerificationRequest(kind=kind, lhs=text, rhs=text)
            ).status
            assert status is not VerificationStatus.TRUE
