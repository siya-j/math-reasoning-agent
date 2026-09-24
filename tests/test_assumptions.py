"""Conditions on the symbols in a claim.

Nearly every identity a scientist wants checked carries them: the Gaussian
integral is sqrt(pi/a) only for a > 0, sqrt(x**2) is x only for x >= 0.
Without a way to state them SymPy correctly returns a Piecewise and the
check is useless.

THE GROUP THAT MATTERS is the last one. An assumption can turn a FALSE
claim TRUE -- `Abs(x) = x` is false, and true given x > 0 -- so a model
free to choose its own assumptions could narrow any claim until it held.
That is the silent-correction failure the faithfulness lint exists for, in
a form the lint cannot see, because it changes no numbers.
"""

import pytest

import verifiers
from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers import assumptions
from verifiers.assumptions import AssumptionError

TRUE = VerificationStatus.TRUE
FALSE = VerificationStatus.FALSE
UNKNOWN = VerificationStatus.UNKNOWN


def equality(lhs: str, rhs: str, assuming: str = ""):
    return verifiers.verify(VerificationRequest(
        kind=VerificationKind.EQUALITY, lhs=lhs, rhs=rhs, assumptions=assuming
    ))


# ----------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    "text, expected",
    [
        ("a > 0", {"a": {"positive": True}}),
        ("a >= 0", {"a": {"nonnegative": True}}),
        ("a < 0", {"a": {"negative": True}}),
        ("a <= 0", {"a": {"nonpositive": True}}),
        ("a != 0", {"a": {"nonzero": True}}),
        ("x real", {"x": {"real": True}}),
        ("x: real", {"x": {"real": True}}),
        ("n positive integer", {"n": {"positive": True, "integer": True}}),
        ("n natural", {"n": {"integer": True, "nonnegative": True}}),
    ],
)
def test_the_forms_a_question_would_use(text, expected):
    assert assumptions.parse(text) == expected


def test_several_symbols_at_once():
    assert assumptions.parse("a > 0, n integer") == {
        "a": {"positive": True}, "n": {"integer": True},
    }


def test_repeated_mentions_of_one_symbol_accumulate():
    assert assumptions.parse("n integer, n > 0") == {
        "n": {"integer": True, "positive": True},
    }


@pytest.mark.parametrize(
    "text", ["x unicorn", "x", "> 0", "1 > 0", "x is purple", "x:"]
)
def test_an_assumption_it_does_not_understand_is_refused(text):
    """Silently dropping one would check a WEAKER claim than the caller
    asked for and say nothing about having done so."""
    with pytest.raises(AssumptionError):
        assumptions.parse(text)


def test_contradictory_assumptions_are_refused_by_sympy():
    with pytest.raises(AssumptionError):
        assumptions.build("x positive, x negative")


def test_nothing_stated_means_nothing_assumed():
    assert assumptions.parse("") == {}
    assert assumptions.parse("   ") == {}


# ------------------------------------------------- what they make decidable
def test_the_gaussian_integral_needs_a_positive_parameter():
    """The case that motivated this. Without the condition SymPy returns a
    Piecewise and correctly refuses to commit."""
    assert equality(
        "integrate(exp(-a*x**2), (x, -oo, oo))", "sqrt(pi/a)"
    ).status is UNKNOWN
    assert equality(
        "integrate(exp(-a*x**2), (x, -oo, oo))", "sqrt(pi/a)", "a > 0"
    ).status is TRUE


def test_a_square_root_of_a_square():
    assert equality("sqrt(x**2)", "x", "x >= 0").status is TRUE


def test_an_even_power_of_minus_one():
    assert equality("(-1)**(2*n)", "1", "n positive integer").status is TRUE


# ============================================================== THE HAZARD
def test_a_conditional_truth_always_carries_its_condition():
    """`Abs(x) = x` is FALSE. Given x > 0 it is TRUE. Both are correct
    answers to different questions, so the verdict must say which question
    it answered -- the detail line is where a reader catches a claim that
    was quietly narrowed until it held."""
    verdict = equality("Abs(x)", "x", "x > 0")
    assert verdict.status is TRUE
    assert "ASSUMING x is positive" in verdict.detail


def test_the_condition_is_stated_on_refutations_too():
    verdict = equality("x**2", "x", "x > 1")
    assert "ASSUMING" in verdict.detail


def test_nothing_is_assumed_by_default():
    """A claim with no stated assumptions is checked over the widest domain
    SymPy will consider. That is the strict reading, and the safe one."""
    assert "ASSUMING" not in equality("sqrt(x**2)", "x").detail
    assert equality("sqrt(x**2)", "x").status is not TRUE


def test_unreadable_assumptions_refuse_rather_than_ignore():
    """Dropping them would answer a narrower question than the one asked,
    and report it as though it were the one asked."""
    verdict = equality("Abs(x)", "x", "x unicorn")
    assert verdict.status is UNKNOWN
    assert "Could not read the assumptions" in verdict.detail


# ------------------------------------------------------------ not leaking
def test_an_assumption_cannot_shadow_a_function_name():
    """`pi > 0` must not replace SymPy's pi with a bare symbol, which would
    silently change every expression that uses it."""
    verdict = equality("sin(pi)", "0", "pi > 0")
    assert verdict.status is TRUE


def test_assumptions_do_not_persist_between_requests():
    """They are rebuilt on every verify(). A condition carried over would
    decide a later claim under one nobody stated."""
    assert equality("Abs(x)", "x", "x > 0").status is TRUE
    later = equality("Abs(x)", "x")
    assert later.status is not TRUE
    assert "ASSUMING" not in later.detail


def test_the_other_kinds_still_work_without_assumptions():
    for kind, lhs, rhs in [
        (VerificationKind.PRIMALITY, "97", ""),
        (VerificationKind.NUMERIC, "2+2", "4"),
    ]:
        assert verifiers.verify(
            VerificationRequest(kind=kind, lhs=lhs, rhs=rhs)
        ).status is TRUE


def test_the_tool_passes_assumptions_through():
    from pipeline.tools import VerificationLog, make_tools

    log = VerificationLog()
    tool = {t.__name__: t for t in make_tools(log)}["check_equality"]
    tool("a claim", "sqrt(x**2)", "x", "x >= 0")
    assert log.checks[-1].request.assumptions == "x >= 0"
    assert log.checks[-1].verdict.status is TRUE
