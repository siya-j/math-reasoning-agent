"""Offline tests for the deterministic half. No API key, no network."""

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers import verify


def req(kind, lhs="", rhs="", variable="x", candidate="", point=""):
    return VerificationRequest(
        kind=kind,
        lhs=lhs,
        rhs=rhs,
        variable=variable,
        candidate=candidate,
        point=point,
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


def test_binomial_counterexample_is_false():
    v = verify(req(VerificationKind.EQUALITY, "(a+b)**2", "a**2 + b**2"))
    assert v.status is VerificationStatus.FALSE


# ------------------------------- regressions from the first full eval run
def test_constant_of_integration_is_not_a_counterexample():
    """Was a soundness failure: C is unbound, so this is not well posed."""
    v = verify(req(VerificationKind.EQUALITY, "integrate(2*x, x)", "x**2 + C"))
    assert v.status is VerificationStatus.UNKNOWN
    assert not v.was_verified


def test_invented_symbol_is_not_decided_numerically():
    """Was a soundness failure: the agent invented a symbol, we ruled on it."""
    v = verify(
        req(VerificationKind.NUMERIC, "maximum_of_function_on_compact_set", "oo")
    )
    assert v.status is VerificationStatus.UNKNOWN
    assert not v.was_verified


def test_lowercase_i_is_refused_not_declared_false():
    """Was a soundness failure: lowercase i parses as a symbol, not sqrt(-1)."""
    v = verify(
        req(VerificationKind.SOLUTION, "x**2 + 1", "0", candidate="i, -i")
    )
    assert v.status is VerificationStatus.UNKNOWN
    assert not v.was_verified


def test_capital_I_is_accepted_as_the_imaginary_unit():
    v = verify(
        req(VerificationKind.SOLUTION, "x**2 + 1", "0", candidate="I, -I")
    )
    assert v.status is VerificationStatus.TRUE


def test_symbolic_solutions_using_equation_symbols_are_allowed():
    """sqrt(a) is a legitimate solution of x**2 = a; do not refuse it."""
    v = verify(
        req(VerificationKind.SOLUTION, "x**2", "a", candidate="sqrt(a), -sqrt(a)")
    )
    assert v.status is VerificationStatus.TRUE


def test_symbolic_claim_sent_to_the_numeric_checker_is_refused():
    v = verify(req(VerificationKind.NUMERIC, "x + 1", "2"))
    assert v.status is VerificationStatus.UNKNOWN


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


def test_pseudoprime_is_still_correctly_composite():
    v = verify(req(VerificationKind.PRIMALITY, "3215031751"))
    assert v.status is VerificationStatus.FALSE


# ------------------------------------------------------------------ solution
def test_correct_solutions_are_true():
    v = verify(req(VerificationKind.SOLUTION, "x**2", "4", candidate="2, -2"))
    assert v.status is VerificationStatus.TRUE


def test_incomplete_solutions_are_false():
    v = verify(req(VerificationKind.SOLUTION, "x**2", "4", candidate="2"))
    assert v.status is VerificationStatus.FALSE


# --------------------------------------------------------------------- limit
def test_standard_limit_is_true():
    v = verify(req(VerificationKind.LIMIT, "sin(x)/x", "1", point="0"))
    assert v.status is VerificationStatus.TRUE


def test_wrong_limit_value_is_false():
    v = verify(req(VerificationKind.LIMIT, "sin(x)/x", "2", point="0"))
    assert v.status is VerificationStatus.FALSE


def test_limit_at_infinity_is_supported():
    v = verify(req(VerificationKind.LIMIT, "1/x", "0", point="oo"))
    assert v.status is VerificationStatus.TRUE


def test_limit_definition_of_e_is_true():
    v = verify(req(VerificationKind.LIMIT, "(1+1/x)**x", "E", point="oo"))
    assert v.status is VerificationStatus.TRUE


def test_oscillating_function_has_no_limit_and_is_not_decided():
    """It neither equals the claim nor differs from it: refuse, don't guess."""
    v = verify(req(VerificationKind.LIMIT, "sin(1/x)", "0", point="0"))
    assert v.status is VerificationStatus.UNKNOWN
    assert not v.was_verified


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
