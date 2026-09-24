"""Refutation by impossibility, and equation balancing (Phase 3).

These two verifiers are built to opposite rules, and the tests are arranged
to make that visible:

  plausibility  may ONLY refute. Being possible is not being correct, and
                the guard reads any TRUE as a check that passed.
  balance       may do both. Atom counts either match or they do not.
"""

import pytest

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from science import domains
from verifiers.chemistry_verifier import ChemistryVerifier
from verifiers.plausibility_verifier import PlausibilityVerifier

PLAUSIBILITY = PlausibilityVerifier()
CHEMISTRY = ChemistryVerifier()


def possible(quantity: str, value: str) -> VerificationStatus:
    return PLAUSIBILITY.verify(
        VerificationRequest(
            kind=VerificationKind.PLAUSIBILITY, lhs=quantity, rhs=value
        )
    ).status


def balances(equation: str) -> VerificationStatus:
    return CHEMISTRY.verify(
        VerificationRequest(kind=VerificationKind.BALANCE, lhs=equation)
    ).status


# --------------------------------------------------- refutation, the point
@pytest.mark.parametrize(
    "quantity, value",
    [
        ("probability", "1.4"),
        ("probability", "-0.2"),
        ("efficiency", "1.2"),
        ("percentage yield", "120"),
        ("concentration", "-0.5"),
        ("mass", "-3"),
        ("absolute temperature", "-10"),
        ("celsius temperature", "-300"),
        ("speed", "4e8"),
        ("mole fraction", "1.5"),
        ("amount of substance", "-2"),
        ("pH", "150"),
    ],
)
def test_an_impossible_value_is_refuted(quantity, value):
    """This catches the error arithmetic checking cannot see: a calculation
    performed correctly from a formula assembled backwards."""
    assert possible(quantity, value) is VerificationStatus.FALSE


# ------------------------------------------------- the asymmetry, the design
@pytest.mark.parametrize(
    "quantity, value",
    [
        ("probability", "0.3"),
        ("efficiency", "0.4"),
        ("concentration", "3"),
        ("absolute temperature", "298"),
        ("speed", "30"),
        ("pH", "7"),
    ],
)
def test_a_possible_value_is_never_confirmed(quantity, value):
    """A concentration of 3 mol/L is not CORRECT for being possible.

    If this returned TRUE the guard would count it as a passed check, and
    every plausible wrong answer would be promoted to verified.
    """
    assert possible(quantity, value) is VerificationStatus.UNKNOWN


def test_this_verifier_can_never_return_true():
    """Stated as a property, not a sample: no input reaches TRUE."""
    for quantity in domains.known_quantities():
        for value in ("-1e9", "-1", "0", "0.5", "1", "100", "1e9"):
            assert possible(quantity, value) is not VerificationStatus.TRUE


def test_a_quantity_with_no_physical_bound_gets_no_verdict():
    """Energy can be negative; enthalpy routinely is. Inventing a bound
    would refute correct answers at the authority of a refutation."""
    assert possible("energy", "-5") is VerificationStatus.UNKNOWN
    assert possible("enthalpy change", "-100") is VerificationStatus.UNKNOWN
    assert possible("charge", "-1.6e-19") is VerificationStatus.UNKNOWN


def test_the_bounds_that_are_inclusive_are_the_attainable_ones():
    assert possible("probability", "0") is VerificationStatus.UNKNOWN
    assert possible("probability", "1") is VerificationStatus.UNKNOWN
    assert possible("absolute temperature", "0") is VerificationStatus.UNKNOWN
    # Nothing massive reaches the speed of light.
    assert possible("speed", "299792458") is VerificationStatus.FALSE


def test_a_school_bound_is_not_imposed_on_ph():
    """pH is not confined to 0-14. Concentrated HCl is about -1.1 and
    saturated NaOH about 15. Bounding at 0 and 14 refutes real solutions."""
    assert possible("pH", "-1") is VerificationStatus.UNKNOWN
    assert possible("pH", "15") is VerificationStatus.UNKNOWN


def test_a_non_number_gets_no_verdict():
    assert possible("probability", "about a half") is VerificationStatus.UNKNOWN
    assert possible("probability", "1/2") is VerificationStatus.UNKNOWN


# ------------------------------------------------------------------ balance
@pytest.mark.parametrize(
    "equation",
    [
        "2H2 + O2 -> 2H2O",
        "CH4 + 2O2 -> CO2 + 2H2O",
        "2Na + Cl2 -> 2NaCl",
        "Ca(OH)2 + 2HCl -> CaCl2 + 2H2O",
        "N2 + 3H2 -> 2NH3",
        "C6H12O6 + 6O2 -> 6CO2 + 6H2O",
    ],
)
def test_a_balanced_equation_is_confirmed(equation):
    assert balances(equation) is VerificationStatus.TRUE


@pytest.mark.parametrize(
    "equation",
    [
        "H2 + O2 -> H2O",
        "CH4 + O2 -> CO2 + H2O",
        "Na + Cl2 -> NaCl",
        "N2 + H2 -> NH3",
    ],
)
def test_an_unbalanced_equation_is_refuted(equation):
    assert balances(equation) is VerificationStatus.FALSE


def test_the_refutation_names_the_element_that_is_short():
    verdict = CHEMISTRY.verify(
        VerificationRequest(kind=VerificationKind.BALANCE, lhs="H2 + O2 -> H2O")
    )
    assert verdict.status is VerificationStatus.FALSE
    assert "O" in verdict.detail


def test_other_arrow_spellings_are_understood():
    for arrow in ("->", "=>", "-->", "→", "="):
        assert balances(f"2H2 + O2 {arrow} 2H2O") is VerificationStatus.TRUE


def test_an_unreadable_equation_is_refused_not_guessed():
    for bad in ("", "2H2 + O2", "-> 2H2O", "2Xx + O2 -> 2XxO", "2H2 + O2 -> "):
        assert balances(bad) is VerificationStatus.UNKNOWN


def test_balance_is_necessary_but_not_sufficient_and_says_so():
    verdict = CHEMISTRY.verify(
        VerificationRequest(kind=VerificationKind.BALANCE, lhs="2H2 + O2 -> 2H2O")
    )
    assert verdict.status is VerificationStatus.TRUE
    assert "not sufficient" in verdict.detail


def test_each_verifier_declines_the_other_s_work():
    assert not PLAUSIBILITY.supports(
        VerificationRequest(kind=VerificationKind.BALANCE, lhs="x")
    )
    assert not CHEMISTRY.supports(
        VerificationRequest(kind=VerificationKind.PLAUSIBILITY, lhs="x")
    )
