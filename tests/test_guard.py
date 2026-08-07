"""Offline tests for the guard, the faithfulness lint, and the tools.

The agent loop itself is LangChain's code. What we must test is OUR
guarantee: that the verdict is computed from recorded tool results, checked
for faithfulness, and never influenced by what the model says.
"""

from domain.check import Check
from domain.verdict import Verdict, VerificationStatus as S
from domain.verification import VerificationKind, VerificationRequest
from pipeline import guard
from pipeline.faithfulness import unsupported_numbers
from pipeline.tools import VerificationLog, make_tools


def tools_by_name(log):
    """Positional unpacking breaks every time a tool is added."""
    return {tool.__name__: tool for tool in make_tools(log)}

QUESTION = "is a claim true?"


def check(status, tool="check_equality", claim="some claim", request=None):
    return Check(
        tool=tool,
        claim=claim,
        request=request
        or VerificationRequest(kind=VerificationKind.EQUALITY, lhs="a", rhs="b"),
        verdict=Verdict(status, "sympy", "detail"),
    )


def solution_check(status, candidate):
    return check(
        status,
        tool="solve_equation",
        request=VerificationRequest(
            kind=VerificationKind.SOLUTION,
            lhs="x**2",
            rhs="4",
            variable="x",
            candidate=candidate,
        ),
    )


# ------------------------------------------------------------------ tools
def test_tools_run_the_real_verifier_and_record_the_call():
    log = VerificationLog()
    tools = tools_by_name(log)
    equality = tools['check_equality']
    numeric = tools['check_numeric']
    primality = tools['check_primality']
    solve = tools['solve_equation']
    limit = tools['check_limit']

    assert "TRUE" in equality("d/dx x^3 is 3x^2", "diff(x**3, x)", "3*x**2")
    assert len(log.checks) == 1
    assert log.checks[0].tool == "check_equality"
    assert log.checks[0].verdict.method == "sympy"


def test_every_check_records_the_claim_it_was_testing():
    log = VerificationLog()
    numeric = tools_by_name(log)['check_numeric']
    numeric("2 + 2 equals 4", "2 + 2", "4")
    assert log.checks[0].claim == "2 + 2 equals 4"


def test_each_tool_records_its_own_name():
    log = VerificationLog()
    tools = tools_by_name(log)
    equality = tools['check_equality']
    numeric = tools['check_numeric']
    primality = tools['check_primality']
    solve = tools['solve_equation']
    limit = tools['check_limit']
    equality("c", "x", "x")
    numeric("c", "2 + 2", "4")
    primality("c", "7919")
    solve("c", "x**2", "4", "x", "2, -2")
    limit("c", "sin(x)/x", "x", "0", "1")
    assert [c.tool for c in log.checks] == [
        "check_equality",
        "check_numeric",
        "check_primality",
        "solve_equation",
        "check_limit",
    ]


def test_every_tool_has_a_unique_name_and_a_docstring():
    names = [tool.__name__ for tool in make_tools(VerificationLog())]
    assert len(names) == len(set(names)), "duplicate tool names"
    assert all(tool.__doc__ for tool in make_tools(VerificationLog()))


def test_a_failing_check_is_recorded_as_false():
    log = VerificationLog()
    numeric = tools_by_name(log)['check_numeric']
    assert "FALSE" in numeric("2 + 2 equals 5", "2 + 2", "5")
    assert log.checks[0].verdict.status is S.FALSE


# ------------------------------------------------------ faithfulness lint
def test_lint_flags_a_solution_the_question_never_mentioned():
    """The observed failure: question says 2, the check claims 2 and -2."""
    request = VerificationRequest(
        kind=VerificationKind.SOLUTION, lhs="x**2", rhs="4", candidate="2, -2"
    )
    extra = unsupported_numbers("Is 2 the only solution of x^2 = 4?", request)
    assert extra == ["-2"]


def test_lint_accepts_solutions_the_question_states():
    request = VerificationRequest(
        kind=VerificationKind.SOLUTION, lhs="x**2", rhs="4", candidate="2, -2"
    )
    assert unsupported_numbers(
        "Are the solutions of x^2 = 4 exactly 2 and -2?", request
    ) == []


def test_lint_ignores_non_solution_checks():
    """Other fields legitimately contain values the model derived."""
    request = VerificationRequest(
        kind=VerificationKind.EQUALITY, lhs="diff(x**3, x)", rhs="3*x**2"
    )
    assert unsupported_numbers("What is the derivative of x cubed?", request) == []


def test_guard_downgrades_an_unfaithful_check_instead_of_endorsing_it():
    verdict = guard.decide(
        "Is 2 the only solution of x^2 = 4?", [solution_check(S.TRUE, "2, -2")]
    )
    assert verdict.status is S.UNKNOWN
    assert not verdict.was_verified
    assert "different claim" in verdict.detail


def test_guard_accepts_a_faithful_solution_check():
    verdict = guard.decide(
        "Are the solutions of x^2 = 4 exactly 2 and -2?",
        [solution_check(S.TRUE, "2, -2")],
    )
    assert verdict.status is S.TRUE


# ------------------------------------------------------------------ guard
def test_no_checks_means_not_verified():
    """The agentic failure mode: answering from memory."""
    verdict = guard.decide(QUESTION, [])
    assert verdict.status is S.NOT_APPLICABLE
    assert not verdict.was_verified


def test_all_true_is_verified_true():
    assert guard.decide(QUESTION, [check(S.TRUE), check(S.TRUE)]).status is S.TRUE


def test_one_false_outweighs_many_trues():
    checks = [check(S.TRUE), check(S.TRUE), check(S.FALSE), check(S.TRUE)]
    assert guard.decide(QUESTION, checks).status is S.FALSE


def test_partial_success_is_unknown_not_true():
    assert guard.decide(QUESTION, [check(S.TRUE), check(S.UNKNOWN)]).status is S.UNKNOWN


def test_all_unknown_is_unknown():
    assert guard.decide(QUESTION, [check(S.UNKNOWN)]).status is S.UNKNOWN


# ----------------------------------------------------------------- banner
def test_banner_states_the_verdict_and_lists_every_check():
    checks = [check(S.TRUE), check(S.FALSE)]
    text = guard.banner(guard.decide(QUESTION, checks), checks, [])
    assert "VERIFIED FALSE" in text
    assert text.count("check_equality") == 2


def test_banner_shows_the_claim_so_substitution_is_visible():
    checks = [check(S.TRUE, claim="the solutions are 2 and -2")]
    text = guard.banner(guard.decide(QUESTION, checks), checks, [])
    assert "the solutions are 2 and -2" in text


def test_banner_marks_evidence_as_not_proof():
    text = guard.banner(guard.decide(QUESTION, []), [], [check(S.TRUE)])
    assert "NOT proof" in text


def test_banner_says_not_verified_when_nothing_was_checked():
    assert "NOT VERIFIED" in guard.banner(guard.decide(QUESTION, []), [], [])
