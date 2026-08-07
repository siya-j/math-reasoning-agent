"""Offline tests for the deterministic half. No API key, no network."""

from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers import verify


def req(
    kind, lhs="", rhs="", variable="x", candidate="", point="", order="", relation=""
):
    return VerificationRequest(
        kind=kind,
        lhs=lhs,
        rhs=rhs,
        variable=variable,
        candidate=candidate,
        point=point,
        order=order,
        relation=relation,
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


# -------------------------------------------------------------------- series
def test_maclaurin_series_of_exp_is_true():
    v = verify(
        req(VerificationKind.SERIES, "exp(x)", "1 + x + x**2/2 + x**3/6",
            point="0", order="4")
    )
    assert v.status is VerificationStatus.TRUE


def test_truncated_series_is_false():
    v = verify(req(VerificationKind.SERIES, "exp(x)", "1 + x", point="0", order="4"))
    assert v.status is VerificationStatus.FALSE


def test_series_of_sin_is_true():
    v = verify(
        req(VerificationKind.SERIES, "sin(x)", "x - x**3/6", point="0", order="5")
    )
    assert v.status is VerificationStatus.TRUE


# -------------------------------------------------------------------- matrix
def test_matrix_product_with_identity_is_true():
    v = verify(
        req(VerificationKind.MATRIX,
            "Matrix([[1,2],[3,4]])*Matrix([[1,0],[0,1]])",
            "Matrix([[1,2],[3,4]])")
    )
    assert v.status is VerificationStatus.TRUE


def test_different_matrices_are_false():
    v = verify(
        req(VerificationKind.MATRIX, "Matrix([[1,2],[3,4]])", "Matrix([[1,2],[3,5]])")
    )
    assert v.status is VerificationStatus.FALSE


def test_mismatched_shapes_are_false():
    v = verify(req(VerificationKind.MATRIX, "Matrix([[1,2]])", "Matrix([[1],[2]])"))
    assert v.status is VerificationStatus.FALSE


def test_scalar_sent_to_the_matrix_checker_is_refused():
    v = verify(req(VerificationKind.MATRIX, "det(Matrix([[1,2],[3,4]]))", "-2"))
    assert v.status is VerificationStatus.UNKNOWN


# ---------------------------------------------------------------- inequality
def test_square_is_non_negative_for_all_reals():
    v = verify(req(VerificationKind.INEQUALITY, "x**2", "0", relation=">="))
    assert v.status is VerificationStatus.TRUE


def test_strict_positivity_of_a_square_is_false_at_zero():
    """x**2 > 0 fails at exactly one point, and the check should say where."""
    v = verify(req(VerificationKind.INEQUALITY, "x**2", "0", relation=">"))
    assert v.status is VerificationStatus.FALSE
    assert "0" in v.detail


def test_inequality_reports_the_interval_where_it_fails():
    v = verify(req(VerificationKind.INEQUALITY, "x**2", "1", relation=">="))
    assert v.status is VerificationStatus.FALSE
    assert "-1" in v.detail


def test_numeric_inequality_needs_no_variable():
    assert verify(
        req(VerificationKind.INEQUALITY, "3", "2", relation=">")
    ).status is VerificationStatus.TRUE
    assert verify(
        req(VerificationKind.INEQUALITY, "2", "3", relation=">")
    ).status is VerificationStatus.FALSE


def test_unsupported_relation_is_refused():
    v = verify(req(VerificationKind.INEQUALITY, "x", "0", relation="!="))
    assert v.status is VerificationStatus.UNKNOWN


def test_multivariable_inequality_is_refused():
    v = verify(req(VerificationKind.INEQUALITY, "x + y", "0", relation=">="))
    assert v.status is VerificationStatus.UNKNOWN


# ------------------------------------------------------------- factorization
def test_prime_factorization_is_true():
    v = verify(req(VerificationKind.FACTORIZATION, "360", "2**3 * 3**2 * 5"))
    assert v.status is VerificationStatus.TRUE


def test_repeated_factors_written_out_are_accepted():
    v = verify(req(VerificationKind.FACTORIZATION, "360", "2*2*2*3*3*5"))
    assert v.status is VerificationStatus.TRUE


def test_composite_factors_are_false_even_though_the_product_is_right():
    """8 * 45 = 360, but neither factor is prime, so it is not THE factorisation."""
    v = verify(req(VerificationKind.FACTORIZATION, "360", "8 * 45"))
    assert v.status is VerificationStatus.FALSE
    assert "not prime" in v.detail


def test_factorization_with_the_wrong_product_is_false():
    v = verify(req(VerificationKind.FACTORIZATION, "360", "2**3 * 3**2"))
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
