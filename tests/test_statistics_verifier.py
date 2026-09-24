"""Distributions and statistical tests (Phase 4).

The group that matters most is the last one. A p-value computed from
malformed input is a number with no meaning, and a number with no meaning is
the most dangerous output this system can produce, because it is
indistinguishable from one that means something.
"""

import pytest

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers.statistics_verifier import StatisticsVerifier, parse_parameters

VERIFIER = StatisticsVerifier()


def decide(statistic: str, parameters: str, claimed: str) -> VerificationStatus:
    return VERIFIER.verify(
        VerificationRequest(
            kind=VerificationKind.STATISTIC,
            lhs=statistic,
            rhs=claimed,
            parameters=parameters,
        )
    ).status


# ------------------------------------------------------------------ parsing
def test_parameters_are_read_as_names_and_values():
    assert parse_parameters("n=10, p=0.5, k=5") == {"n": 10.0, "p": 0.5, "k": 5.0}


def test_lists_survive_the_comma_splitting():
    """Splitting naively on commas would tear the lists apart."""
    assert parse_parameters("observed=[10, 20], expected=[15, 15]") == {
        "observed": [10.0, 20.0],
        "expected": [15.0, 15.0],
    }


# ----------------------------------------------------------------- binomial
def test_a_binomial_probability_is_exact():
    """Ten fair coins landing five heads is 63/256, and sympy.stats gives
    that as a rational rather than a float with rounding in it."""
    assert decide("binomial probability", "n=10, p=0.5, k=5", "0.24609375") is (
        VerificationStatus.TRUE
    )


def test_a_monohybrid_cross_is_three_quarters_dominant():
    """Aa x Aa: at least one dominant allele in three of four outcomes."""
    assert decide("binomial at least", "n=1, p=0.75, k=1", "0.75") is (
        VerificationStatus.TRUE
    )


def test_the_tails_are_not_the_same_number():
    assert decide("binomial at most", "n=10, p=0.5, k=3", "0.171875") is (
        VerificationStatus.TRUE
    )
    assert decide("binomial at least", "n=10, p=0.5, k=3", "0.171875") is (
        VerificationStatus.FALSE
    )


def test_a_wrong_probability_is_refuted():
    assert decide("binomial probability", "n=10, p=0.5, k=5", "0.5") is (
        VerificationStatus.FALSE
    )


# ------------------------------------------------------------------- normal
def test_the_normal_tail_is_computed():
    assert decide("normal below", "mu=0, sigma=1, x=1.96", "0.975") is (
        VerificationStatus.TRUE
    )
    assert decide("normal above", "mu=0, sigma=1, x=1.96", "0.025") is (
        VerificationStatus.TRUE
    )


# --------------------------------------------------------------- chi square
def test_the_chi_square_statistic_is_computed():
    """A 9:3:3:1 dihybrid ratio against observed counts."""
    assert decide(
        "chi square statistic",
        "observed=[100, 30, 30, 15], expected=[98.4375, 32.8125, 32.8125, 10.9375]",
        "2.016",
    ) is VerificationStatus.TRUE


def test_a_p_value_at_the_classic_threshold():
    """chi-square of 3.841 on one degree of freedom is the 0.05 point."""
    assert decide(
        "chi square p value", "observed=[54, 46], expected=[50, 50]", "0.4237"
    ) is VerificationStatus.TRUE


def test_the_p_value_is_reported_but_not_interpreted():
    """Where the threshold sits is a judgement about the experiment. The
    guard must not be handed judgements dressed as verdicts."""
    verdict = VERIFIER.verify(
        VerificationRequest(
            kind=VerificationKind.STATISTIC,
            lhs="chi square p value",
            rhs="0.4237",
            parameters="observed=[54, 46], expected=[50, 50]",
        )
    )
    assert "judgement" in verdict.detail
    assert "significant" not in verdict.detail.replace(
        "counts as significant", ""
    )


# ------------------------------------------------------------------- t test
def test_a_two_sided_t_p_value():
    assert decide("t test p value", "t=2.228, df=10", "0.05") is (
        VerificationStatus.TRUE
    )


# ------------------------------------------------- what it refuses to compute
@pytest.mark.parametrize(
    "statistic, parameters",
    [
        ("binomial probability", "n=10, p=1.5, k=5"),     # p outside [0, 1]
        ("binomial probability", "n=10, p=0.5, k=11"),    # k exceeds n
        ("binomial probability", "n=-4, p=0.5, k=1"),     # negative n
        ("binomial probability", "n=10.5, p=0.5, k=5"),   # n not whole
        ("binomial probability", "n=10, p=0.5"),          # k missing
        ("normal below", "mu=0, sigma=0, x=1"),           # zero spread
        ("normal below", "mu=0, sigma=-1, x=1"),          # negative spread
        ("chi square p value", "observed=[10], expected=[10]"),   # one category
        ("chi square p value", "observed=[10,20], expected=[10]"),  # mismatched
        ("chi square p value", "observed=[10,20], expected=[0,30]"),  # zero expected
        ("chi square p value", "observed=[-1,20], expected=[10,9]"),  # negative
        ("t test p value", "t=2.0, df=0"),                # no degrees of freedom
    ],
)
def test_malformed_parameters_are_refused_not_computed(statistic, parameters):
    assert decide(statistic, parameters, "0.5") is VerificationStatus.UNKNOWN


def test_totals_that_disagree_are_refused():
    """A goodness-of-fit test compares one total against the way it was
    predicted to divide. Different totals are not the same experiment, and
    the statistic computed from them means nothing."""
    assert decide(
        "chi square p value", "observed=[50, 50], expected=[30, 30]", "0.5"
    ) is VerificationStatus.UNKNOWN


def test_an_unknown_statistic_gets_no_verdict():
    assert decide("vibes test", "n=1", "0.5") is VerificationStatus.UNKNOWN


def test_a_claimed_value_that_is_not_a_number_gets_no_verdict():
    assert decide(
        "binomial probability", "n=10, p=0.5, k=5", "about a quarter"
    ) is VerificationStatus.UNKNOWN


def test_nothing_raises_and_nothing_guesses():
    for parameters in ("", "((", "n=", "=5", "observed=[]"):
        status = decide("binomial probability", parameters, "0.5")
        assert status is not VerificationStatus.TRUE


def test_it_declines_kinds_that_are_not_its_business():
    assert not VERIFIER.supports(
        VerificationRequest(kind=VerificationKind.PRIMALITY, lhs="7")
    )
    assert VERIFIER.supports(
        VerificationRequest(kind=VerificationKind.STATISTIC, lhs="binomial")
    )
