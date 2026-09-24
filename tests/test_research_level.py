"""Checks a working scientist needs, not exercises a student needs.

WHY THIS FILE EXISTS. eval/golden-science.json is undergraduate: molar
masses, Ohm's law, Hardy-Weinberg. Nobody with a doctorate needs help
computing 2 x 6.022e23, and the owner said so.

The deeper problem is not that the cases are easy. It is that EVERY ONE OF
THEM HAS UNITS THAT SURVIVE TO THE END, so not one of them exercised the
path where a quantity cancels to dimensionless. Probing the machinery
against paper-grade checks failed six times out of eight, including the
single most common research-level dimensional question -- "is this group
dimensionless?" -- which was refused outright.

A benchmark of basic science cannot distinguish machinery that works from
machinery that only works on easy inputs.

These are unit tests rather than golden cases on purpose: they test what the
VERIFIERS can decide, cost nothing to run, and fail loudly in CI. Whether the
model calls them correctly is the separate question the golden set answers.

WHAT IS STILL MISSING is recorded at the bottom, as xfail, so the gaps are
visible in test output rather than in a document nobody opens.
"""

import pytest

import verifiers
from domain.verdict import VerificationStatus
from domain.verification import VerificationKind as K
from domain.verification import VerificationRequest as R


def decide(**kwargs) -> VerificationStatus:
    return verifiers.verify(R(**kwargs)).status


TRUE = VerificationStatus.TRUE
FALSE = VerificationStatus.FALSE
UNKNOWN = VerificationStatus.UNKNOWN


# ------------------------------------------------- dimensionless groups
@pytest.mark.parametrize(
    "name, expression",
    [
        ("Reynolds",
         "1000*(kilogram/meter**3)*2*(meter/second)*0.1*meter"
         "/(0.001*pascal*second)"),
        ("Peclet", "(meter/second)*meter/(meter**2/second)"),
        ("Mach", "(meter/second)/(meter/second)"),
        ("strain", "meter/meter"),
        ("Damkohler", "(1/second)*second"),
    ],
)
def test_a_dimensionless_group_is_recognised_as_dimensionless(name, expression):
    """The commonest dimensional question in transport, fluids and kinetics.

    These all CANCEL, which is precisely why they were refused before: the
    check looked for surviving units and found none.
    """
    assert decide(kind=K.DIMENSION, lhs=expression, rhs="1") is TRUE


def test_a_group_that_does_not_cancel_is_not_dimensionless():
    """The check must still be able to say no."""
    assert decide(
        kind=K.DIMENSION, lhs="(meter/second)*meter/(meter**2)", rhs="1"
    ) is FALSE


# --------------------------------- transcendental arguments must be pure
@pytest.mark.parametrize(
    "expression",
    [
        "exp(5*joule)",
        "log(5*meter)",
        "sin(2*second)",
        "exp(1000*(joule/mole))",
    ],
)
def test_a_dimensional_argument_to_a_transcendental_is_refuted(expression):
    """The error that reaches published papers.

    exp is defined by a series that adds its argument to its own powers, so
    a dimensional argument is not merely unusual, it is meaningless. Before
    this was checked the verifier said UNKNOWN, which reads as "unsettled"
    for something definitely wrong.
    """
    assert decide(kind=K.DIMENSION, lhs=expression, rhs="1") is FALSE


def test_the_boltzmann_factor_is_accepted_when_the_units_match():
    """E/kT with both per-particle: dimensionless, and fine."""
    assert decide(
        kind=K.DIMENSION,
        lhs="exp(1.6e-19*joule/(1.380649e-23*(joule/kelvin)*300*kelvin))",
        rhs="1",
    ) is TRUE


def test_per_mole_over_per_particle_is_caught():
    """THE unit trap: an activation energy per MOLE divided by a Boltzmann
    constant per PARTICLE leaves a stray amount-of-substance."""
    assert decide(
        kind=K.DIMENSION,
        lhs="exp(50000*(joule/mole)/(1.380649e-23*(joule/kelvin)*298*kelvin))",
        rhs="1",
    ) is FALSE


def test_the_arrhenius_factor_with_the_gas_constant_is_fine():
    """The same quantity done correctly, with R rather than k_B."""
    assert decide(
        kind=K.DIMENSION,
        lhs="exp(50000*(joule/mole)/(8.314*(joule/(mole*kelvin))*298*kelvin))",
        rhs="1",
    ) is TRUE


# ------------------------------------------------------ real-unit traps
def test_energy_per_mole_is_not_energy():
    assert decide(
        kind=K.DIMENSION, lhs="joule/mole", rhs="joule"
    ) is FALSE


def test_a_diffusion_coefficient_has_area_over_time():
    assert decide(
        kind=K.DIMENSION, lhs="meter**2/second", rhs="centimeter**2/second"
    ) is TRUE


def test_a_rate_constant_is_not_a_rate():
    """First-order kinetics: k is per second, the rate is per second per
    volume. Confusing them is a standard undergraduate-to-postgraduate
    error that survives into papers."""
    assert decide(
        kind=K.DIMENSION, lhs="1/second", rhs="mole/(liter*second)"
    ) is FALSE


def test_spectroscopic_wavenumber_is_inverse_length():
    assert decide(
        kind=K.DIMENSION, lhs="1/centimeter", rhs="1/meter"
    ) is TRUE


def test_a_photon_energy_from_a_wavenumber_needs_h_and_c():
    assert decide(
        kind=K.DIMENSION,
        lhs="6.62607015e-34*joule*second*299792458*(meter/second)*(1/meter)",
        rhs="joule",
    ) is TRUE


# ------------------------------------------------------------ statistics
def test_a_goodness_of_fit_across_four_categories():
    assert decide(
        kind=K.STATISTIC, lhs="chi square statistic",
        parameters="observed=[10,20,30,40], expected=[25,25,25,25]",
        rhs="20",
    ) is TRUE


def test_a_test_whose_totals_disagree_is_refused_not_computed():
    """Two different experiments. The statistic means nothing."""
    assert decide(
        kind=K.STATISTIC, lhs="chi square p value",
        parameters="observed=[50,50], expected=[30,30]", rhs="0.5",
    ) is UNKNOWN


# =======================================================================
#  STILL MISSING. Recorded as failing tests so the gaps show up in test
#  output rather than in a document nobody opens. Each is a capability a
#  doctoral user would expect, and none of them exists yet.
# =======================================================================

@pytest.mark.xfail(reason="no uncertainty propagation: the biggest gap for "
                          "research use, where every quantity has error bars",
                   strict=True)
def test_a_value_agrees_with_a_measurement_within_its_uncertainty():
    """9.81 +/- 0.02 and 9.80665 are the same measurement. Today the
    verifier compares them as exact numbers and returns FALSE."""
    assert decide(
        kind=K.QUANTITY,
        lhs="9.81*meter/second**2",
        rhs="9.80665*meter/second**2",
        tolerance="uncertainty:0.02",
    ) is TRUE


@pytest.mark.xfail(reason="no way to state assumptions, so SymPy returns a "
                          "Piecewise and the identity cannot be decided",
                   strict=True)
def test_a_gaussian_integral_can_be_checked_given_a_positive_parameter():
    """Every derivation a physicist wants checked carries assumptions --
    a > 0, x real, n a positive integer. Without them SymPy correctly
    refuses to commit, and the check is useless."""
    assert decide(
        kind=K.EQUALITY,
        lhs="integrate(exp(-a*x**2), (x, -oo, oo))",
        rhs="sqrt(pi/a)",
    ) is TRUE
