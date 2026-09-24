"""Propagating measurement uncertainty.

The gap that most separates textbook science from research science: a
textbook number is exact, a measured one is not, and every quantity a
working scientist computes carries an error bar.

The first group is the one to read. Symbolic differentiation is not a
stylistic choice here — it is what makes a cancelled variable contribute
nothing, where interval arithmetic would invent an error bar for it.
"""

import pytest

import verifiers
from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from science.uncertainty import (
    Measurement,
    UncertaintyError,
    parse_measurement,
    parse_measurements,
    propagate,
)

TRUE = VerificationStatus.TRUE
FALSE = VerificationStatus.FALSE
UNKNOWN = VerificationStatus.UNKNOWN


def result(formula: str, measurements: str) -> Measurement:
    return propagate(formula, parse_measurements(measurements)).result


def decide(formula: str, measurements: str, claimed: str) -> VerificationStatus:
    return verifiers.verify(VerificationRequest(
        kind=VerificationKind.UNCERTAINTY,
        lhs=formula, rhs=claimed, parameters=measurements,
    )).status


# ============================================================ correlation
def test_a_variable_that_cancels_contributes_no_uncertainty():
    """THE reason this uses symbolic differentiation.

    Interval arithmetic treats each occurrence of x as independent and gives
    `x - x` an uncertainty of s*sqrt(2). Differentiating the expression
    first, d(x - x)/dx is 0 before any number is substituted, so the answer
    is exactly zero with no error bar.
    """
    assert result("x - x", "x=5 +/- 1") == Measurement(0.0, 0.0)


def test_a_repeated_variable_is_not_double_counted():
    """x*x at x = 5 +/- 1 has d/dx = 2x = 10, so s = 10 -- the relative
    uncertainty doubles, exactly once. Treating the two x's separately would
    give 5*sqrt(2) instead."""
    assert result("x*x", "x=5 +/- 1").uncertainty == pytest.approx(10.0)


def test_a_ratio_of_the_same_variable_is_exact():
    assert result("x/x", "x=5 +/- 1").uncertainty == pytest.approx(0.0)


# ================================================================ the maths
def test_a_sum_adds_in_quadrature():
    """s = sqrt(0.3^2 + 0.4^2) = 0.5, not 0.7."""
    assert result("a + b", "a=1 +/- 0.3, b=2 +/- 0.4").uncertainty == (
        pytest.approx(0.5)
    )


def test_a_product_uses_the_partial_derivatives():
    """d/da = b = 3, d/db = a = 2 -> sqrt((3*0.1)^2 + (2*0.2)^2) = 0.5."""
    assert result("a*b", "a=2 +/- 0.1, b=3 +/- 0.2").uncertainty == (
        pytest.approx(0.5)
    )


def test_kinetic_energy_from_a_measured_mass_and_speed():
    computed = result("m*v**2/2", "m=2 +/- 0.1, v=3 +/- 0.05")
    assert computed.value == pytest.approx(9.0)
    assert computed.uncertainty == pytest.approx(0.5408326913)


def test_an_exact_input_carries_no_uncertainty():
    assert result("a*b", "a=2, b=3") == Measurement(6.0, 0.0)


def test_a_nonlinear_function_is_differentiated_properly():
    """d(exp(x))/dx at 0 is 1, so s passes through unchanged."""
    assert result("exp(x)", "x=0 +/- 0.1").uncertainty == pytest.approx(0.1)


# ============================================================== the parsing
@pytest.mark.parametrize("text", ["9.81 +/- 0.02", "9.81+-0.02", "9.81 ± 0.02"])
def test_every_spelling_of_plus_or_minus(text):
    assert parse_measurement(text) == Measurement(9.81, 0.02)


def test_a_value_with_no_error_bar_is_exact():
    assert parse_measurement("9.81") == Measurement(9.81, 0.0)


@pytest.mark.parametrize(
    "text",
    ["", "nine", "9.81 +/- fuzzy", "9.81 +/- -0.02", "x = 1, x = 2", "1, 2"],
)
def test_input_that_is_not_a_measurement_is_refused(text):
    with pytest.raises(UncertaintyError):
        parse_measurements(text) if "=" in text else parse_measurement(text)


def test_a_formula_using_an_unmeasured_variable_is_refused():
    """Substituting nothing for it would silently treat it as a symbol and
    produce an expression rather than a number."""
    with pytest.raises(UncertaintyError):
        propagate("a*b*c", parse_measurements("a=1 +/- 0.1, b=2 +/- 0.1"))


def test_the_parser_has_no_access_to_the_interpreter():
    with pytest.raises(UncertaintyError):
        propagate("__import__('os')", parse_measurements("a=1"))


# ============================================================== the verdicts
def test_value_and_error_bar_are_both_confirmed():
    assert decide("m*v**2/2", "m=2 +/- 0.1, v=3 +/- 0.05", "9 +/- 0.54") is TRUE


def test_a_right_value_with_a_wrong_error_bar_is_a_wrong_answer():
    """The more dangerous of the two failures: the value looks correct, and
    the error bar is what a reader uses to decide whether a difference
    means anything."""
    assert decide("m*v**2/2", "m=2 +/- 0.1, v=3 +/- 0.05", "9 +/- 0.1") is FALSE


def test_a_wrong_value_is_refuted():
    assert decide("m*v**2/2", "m=2 +/- 0.1, v=3 +/- 0.05", "12 +/- 0.54") is FALSE


def test_a_claim_with_no_error_bar_has_only_its_value_checked():
    assert decide("m*v**2/2", "m=2 +/- 0.1, v=3 +/- 0.05", "9") is TRUE
    assert decide("m*v**2/2", "m=2 +/- 0.1, v=3 +/- 0.05", "12") is FALSE


def test_the_claim_is_judged_at_the_precision_it_was_written_to():
    """0.54 and 0.5408... are the same claim to two figures."""
    assert decide("a/b", "a=10 +/- 0.5, b=2 +/- 0.1", "5.0 +/- 0.35") is TRUE
    assert decide("a/b", "a=10 +/- 0.5, b=2 +/- 0.1", "5.0 +/- 0.99") is FALSE


def test_malformed_input_is_refused_rather_than_propagated():
    """An error bar computed from nonsense looks exactly like one that means
    something."""
    assert decide("m*v", "m=two", "9") is UNKNOWN
    assert decide("", "m=2 +/- 0.1", "9") is UNKNOWN
    assert decide("m*v", "", "9") is UNKNOWN


def test_nothing_reaches_true_by_accident():
    for formula, measurements, claimed in [
        ("((", "a=1", "1"), ("a", "", "1"), ("a", "a=1", ""),
        ("a", "a=1 +/- x", "1"),
    ]:
        assert decide(formula, measurements, claimed) is not TRUE


# ============================================================== the reporting
def test_the_number_is_reported_whatever_the_verdict():
    """A scientist asking this usually wants the number more than the
    verdict, and this is where the guard lets one through honestly: it was
    computed, not asserted."""
    for claimed in ("9 +/- 0.54", "12 +/- 0.1"):
        verdict = verifiers.verify(VerificationRequest(
            kind=VerificationKind.UNCERTAINTY, lhs="m*v**2/2",
            rhs=claimed, parameters="m=2 +/- 0.1, v=3 +/- 0.05",
        ))
        assert "9.0 +/- 0.54" in verdict.detail
        assert "d/dm" in verdict.detail, "the contributions are the evidence"


def test_the_assumptions_are_stated_in_the_verdict():
    """A propagated uncertainty is worthless without them."""
    verdict = verifiers.verify(VerificationRequest(
        kind=VerificationKind.UNCERTAINTY, lhs="a*b",
        rhs="6 +/- 0.4", parameters="a=2 +/- 0.1, b=3 +/- 0.1",
    ))
    assert "uncorrelated" in verdict.detail


def test_a_strained_linearity_assumption_is_flagged():
    verdict = verifiers.verify(VerificationRequest(
        kind=VerificationKind.UNCERTAINTY, lhs="a*b",
        rhs="2 +/- 0.8", parameters="a=1 +/- 0.4, b=2 +/- 0.1",
    ))
    assert "linear approximation" in verdict.detail


def test_exactly_one_verifier_claims_this_kind():
    request = VerificationRequest(kind=VerificationKind.UNCERTAINTY, lhs="a")
    owners = [v.name for v in verifiers.VERIFIERS if v.supports(request)]
    assert owners == ["uncertainty"]


# ==================================================== uncertainty WITH units
# Propagation worked on bare magnitudes and the units verifier worked on
# quantities, and the two did not speak. A research measurement is both: a
# pendulum gives g in m/s^2 with an error bar, not a number with an error bar.
#
# Symbolic differentiation carries units through for free -- d(4 pi^2 L/T^2)/dL
# has units of 1/s^2, and multiplying by an uncertainty in metres gives a
# contribution in m/s^2. The quadrature sum then becomes a dimensional check
# in its own right, because contributions of different dimensions cannot be
# added.

def measured(formula: str, measurements: str) -> Measurement:
    return propagate(formula, parse_measurements(measurements)).result


def test_a_pendulum_gives_g_with_units_and_an_error_bar():
    computed = measured(
        "4*pi**2*L/T**2", "L=1.000 +/- 0.005 meter, T=2.006 +/- 0.002 second"
    )
    assert computed.value == pytest.approx(9.8107, abs=1e-3)
    assert computed.uncertainty == pytest.approx(0.0528, abs=1e-3)
    assert computed.unit == "meter/second**2"


def test_the_result_unit_follows_from_the_formula():
    """Nobody states it; it is derived from the inputs."""
    assert measured(
        "m*v**2/2", "m=2 +/- 0.1 kilogram, v=3 +/- 0.05 meter/second"
    ).unit == "kilogram*meter**2/second**2"
    assert measured(
        "m/V", "m=27.0 +/- 0.1 gram, V=10.0 +/- 0.2 milliliter"
    ).unit == "gram/milliliter"


def test_inputs_in_different_units_of_the_same_dimension_are_converted():
    """1.0 m + 50 cm is 150 cm, and the error bars combine in one unit."""
    computed = measured("a+b", "a=1.0 +/- 0.01 meter, b=50 +/- 1 centimeter")
    assert computed.value == pytest.approx(150.0)
    assert computed.uncertainty == pytest.approx(2 ** 0.5)


def test_a_formula_that_adds_unlike_quantities_is_refused():
    """THE soundness case. strip_units divides each unit out by substituting
    1 for it, so `1 metre + 2 seconds` collapses to the number 3 and reports
    nothing wrong. An error bar on that would be meaningless while looking
    exactly like one that is not."""
    with pytest.raises(UncertaintyError) as caught:
        measured("a+b", "a=1.0 +/- 0.01 meter, b=2 +/- 0.1 second")
    assert "different dimensions" in str(caught.value)


def test_cancellation_still_works_with_units():
    assert measured("x - x", "x=5 +/- 1 meter").uncertainty == pytest.approx(0.0)


def test_an_error_bar_must_share_its_quantity_s_unit():
    """`1.0 meter +/- 5 second` is not a measurement, it is two."""
    with pytest.raises(UncertaintyError):
        parse_measurement("1.0 meter +/- 5 second")


def test_a_unit_on_either_side_is_understood():
    assert parse_measurement("9.81 +/- 0.02 meter") == Measurement(
        9.81, 0.02, "meter"
    )
    assert parse_measurement("9.81 meter +/- 0.02") == Measurement(
        9.81, 0.02, "meter"
    )


def test_a_value_with_no_error_bar_may_still_carry_a_unit():
    assert parse_measurement("2.5 kilogram") == Measurement(2.5, 0.0, "kilogram")


def test_something_that_is_not_a_unit_is_refused():
    with pytest.raises(UncertaintyError):
        parse_measurement("1.0 +/- 0.1 bananas")


def test_bare_numbers_still_take_the_old_path():
    """The unit machinery must not disturb the case it was added beside."""
    computed = measured("m*v**2/2", "m=2 +/- 0.1, v=3 +/- 0.05")
    assert computed.unit == ""
    assert computed.uncertainty == pytest.approx(0.5408326913)
