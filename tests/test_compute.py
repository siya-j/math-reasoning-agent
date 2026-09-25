"""Working a value out, when nobody stated one to check.

THE POINT OF THE WHOLE FILE is the distinction between COMPUTED and
VERIFIED, so most of these tests are about the words on the banner rather
than the arithmetic.

A checked claim was tested against a value the USER supplied, and that value
independently cross-checks the model's formalisation -- a wrong formula
disagrees with it and surfaces as FALSE. A computed value has no such check:
the engine produced the number, but the model chose the formula. Conflating
the two would hand a wrong formula the authority the guard was built to
withhold from wrong prose.
"""

import pytest

from domain.verdict import Verdict, VerificationStatus
from pipeline import guard
from pipeline.tools import VerificationLog, make_tools
from verifiers.compute import ComputeError, compute


def tool(log):
    return {f.__name__: f for f in make_tools(log)}["compute_value"]


# ------------------------------------------------- the engine follows the inputs
def test_an_error_bar_in_gives_an_error_bar_out():
    done = compute("what is g?", "4*pi**2*L/T**2",
                   "L=1.000 +/- 0.005 meter, T=2.006 +/- 0.002 second")
    assert done.method == "uncertainty"
    assert "9.81" in done.value and "+/-" in done.value
    assert "meter/second**2" in done.value


def test_a_unit_in_gives_a_unit_out():
    done = compute("energy?", "m*v**2/2", "m=2 kilogram, v=3 meter/second")
    assert done.method == "units"
    assert done.value.startswith("9.0")
    assert "kilogram*meter**2/second**2" in done.value


def test_bare_numbers_stay_bare():
    done = compute("product?", "a*b", "a=6, b=7")
    assert done.method == "sympy"
    assert done.value == "42.0"


def test_the_engine_is_chosen_from_the_inputs_not_from_the_model():
    """One less thing the model can get wrong, and the choice stays
    recoverable from the recorded request afterwards."""
    assert compute("q", "a", "a=1").method == "sympy"
    assert compute("q", "a", "a=1 meter").method == "units"
    assert compute("q", "a", "a=1 +/- 0.1").method == "uncertainty"


# --------------------------------------------------------------- what it refuses
@pytest.mark.parametrize(
    "formula, inputs",
    [
        ("", "a=1"),
        ("a*b", ""),
        ("a*b*c", "a=1, b=2"),            # a name with no value
        ("a+b", "a=1 meter, b=2 second"),  # unlike dimensions
        ("__import__('os')", "a=1"),
        ("((", "a=1"),
    ],
)
def test_it_refuses_rather_than_returning_a_shape_like_a_number(formula, inputs):
    """A number derived from a formula the engine could not make sense of is
    worse than no answer, because the reader has no claim of their own to
    notice it against."""
    with pytest.raises(ComputeError):
        compute("a question", formula, inputs)


def test_a_refusal_reaches_the_model_as_words_not_an_exception():
    log = VerificationLog()
    reply = tool(log)("what is it?", "a*b*c", "a=1, b=2")
    assert reply.startswith("COULD NOT COMPUTE")
    assert not log.computations, "a failed computation must not be recorded"


# ------------------------------------------- computations are not checks
def test_a_computation_is_recorded_apart_from_checks():
    """Structural, not a filing convenience: the guard turns checks into a
    verdict about a claim, and a computation answers a question nobody made
    a claim about. It must never be able to contribute to a TRUE."""
    log = VerificationLog()
    tool(log)("what is g?", "a*b", "a=2 meter, b=3")
    assert len(log.computations) == 1
    assert log.checks == []


def test_a_computation_alone_does_not_produce_a_verdict():
    log = VerificationLog()
    tool(log)("what is g?", "a*b", "a=2, b=3")
    verdict = guard.decide("what is g?", log.checks)
    assert verdict.status is VerificationStatus.NOT_APPLICABLE


# ================================================ COMPUTED IS NOT VERIFIED
def test_the_banner_says_computed_and_never_verified():
    log = VerificationLog()
    tool(log)("what is g?", "4*pi**2*L/T**2",
              "L=1.000 +/- 0.005 meter, T=2.006 +/- 0.002 second")
    verdict = guard.decide("what is g?", log.checks)

    text = guard.banner(verdict, log.checks, [], log.computations)

    assert "[COMPUTED via uncertainty]" in text
    assert "VERIFIED TRUE" not in text


def test_the_banner_shows_the_formula_the_model_chose():
    """The reader is the only check a computed value has, so the
    formalisation is printed beside the answer every time."""
    log = VerificationLog()
    tool(log)("what is g?", "4*pi**2*L/T**2",
              "L=1.000 +/- 0.005 meter, T=2.006 +/- 0.002 second")
    text = guard.banner(guard.decide("q", log.checks), log.checks, [],
                        log.computations)

    assert "4*pi**2*L/T**2" in text
    assert "L=1.000 +/- 0.005 meter" in text


def test_the_banner_says_plainly_that_nothing_was_checked():
    """"performed no deterministic verification" printed beside a computed
    number reads as a contradiction rather than as the distinction it is."""
    log = VerificationLog()
    tool(log)("what is g?", "a*b", "a=2, b=3")
    text = guard.banner(guard.decide("q", log.checks), log.checks, [],
                        log.computations)

    assert "Nothing was CHECKED" in text
    assert "Read the formula" in text


def test_a_checked_claim_does_not_get_the_computed_caveat():
    """It has a real cross-check -- the user's own stated value."""
    from domain.check import Check
    from domain.verification import VerificationKind, VerificationRequest

    check = Check(
        tool="check_numeric", claim="2+2=4",
        request=VerificationRequest(kind=VerificationKind.NUMERIC,
                                    lhs="2+2", rhs="4"),
        verdict=Verdict(VerificationStatus.TRUE, "sympy", "it does"),
    )
    text = guard.banner(Verdict(VerificationStatus.TRUE, "sympy", "ok"),
                        [check], [], ())
    assert "Nothing was CHECKED" not in text
    assert "COMPUTED" not in text


def test_the_banner_is_unchanged_when_nothing_was_computed():
    """The existing shape must survive: this is the common path."""
    verdict = Verdict(VerificationStatus.NOT_APPLICABLE, "none", "nothing")
    assert guard.banner(verdict, [], []) == guard.banner(verdict, [], [], ())


# ------------------------------------------------------------- the caveats
def test_the_assumptions_travel_with_the_number():
    done = compute("q", "a*b", "a=2 +/- 0.1, b=3 +/- 0.1")
    assert any("uncorrelated" in c for c in done.caveats)


def test_the_working_is_kept_for_an_uncertain_result():
    done = compute("q", "a*b", "a=2 +/- 0.1, b=3 +/- 0.1")
    assert "d/da" in done.working and "d/db" in done.working


def test_the_report_puts_the_formula_before_the_answer():
    """A reader who skims sees the number either way; one who is checking
    needs the formula first, because it is the part nothing verified."""
    done = compute("q", "a*b", "a=2 meter, b=3")
    report = done.report()
    assert report.index("from") < report.index("gives")
