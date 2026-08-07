"""Verifiers exposed as agent tools.

These are plain Python functions — LangChain reads their names, type hints
and docstrings to tell the model what exists. The docstrings ARE the model's
instructions, so they are written for the model, not for us.

DOCSTRING RULE, learned from an eval regression: do NOT put concrete worked
examples here. A small model copies them verbatim instead of generalising.
After adding `check_equality("...", "diff(x**3, x)", "3*x**2")` as an
example, the agent ran exactly that check for the question "is 2 + 2 = 5?".
Describe the arguments; show syntax with placeholders; never show a
fillable-looking call with real values in it.
"""

from __future__ import annotations

import verifiers
from domain.check import Check
from domain.verification import VerificationKind, VerificationRequest


class VerificationLog:
    """Records every verifier call the agent makes during one run."""

    def __init__(self) -> None:
        self.checks: list[Check] = []

    def record(self, tool: str, claim: str, request: VerificationRequest) -> str:
        """Run the verifier, store the result, and return text for the model."""
        verdict = verifiers.verify(request)
        self.checks.append(
            Check(tool=tool, claim=claim.strip(), request=request, verdict=verdict)
        )
        return f"{verdict.status.value.upper()}: {verdict.detail}"


def make_tools(log: VerificationLog) -> list:
    """Build the tool functions, bound to one run's log."""

    def check_equality(claim: str, lhs: str, rhs: str) -> str:
        """Check whether two expressions are equal for all values of the variable.

        Use for derivatives, integrals and algebraic identities.

        claim: the claim from the user's question that this check is testing.
        lhs: the left expression, taken from the user's question.
        rhs: the right expression, taken from the user's question.

        Both sides use SymPy syntax: ** for powers, explicit multiplication
        (write 2*x, never 2x), diff(<expr>, <var>) for a derivative,
        integrate(<expr>, <var>) for an integral, sin/cos/log/exp/sqrt/pi.
        """
        return log.record(
            "check_equality",
            claim,
            VerificationRequest(kind=VerificationKind.EQUALITY, lhs=lhs, rhs=rhs),
        )

    def check_numeric(claim: str, expression: str, expected: str) -> str:
        """Check whether a numeric expression evaluates to an expected number.

        Use only when both sides are concrete numbers, with no variables.

        claim: the claim from the user's question that this check is testing.
        expression: the arithmetic from the user's question.
        expected: the value the user's question says it equals.
        """
        return log.record(
            "check_numeric",
            claim,
            VerificationRequest(
                kind=VerificationKind.NUMERIC, lhs=expression, rhs=expected
            ),
        )

    def check_primality(claim: str, n: str) -> str:
        """Check whether an integer is prime.

        Never answer a primality question from memory. Always call this.

        claim: the claim from the user's question that this check is testing.
        n: the integer from the user's question, as a string of digits.
        """
        return log.record(
            "check_primality",
            claim,
            VerificationRequest(kind=VerificationKind.PRIMALITY, lhs=n),
        )

    def solve_equation(
        claim: str, lhs: str, rhs: str, variable: str, claimed_solutions: str
    ) -> str:
        """Check whether the solutions of lhs = rhs are EXACTLY those claimed.

        claim: the claim from the user's question that this check is testing.
        lhs, rhs: the two sides of the equation, in SymPy syntax.
        variable: the variable being solved for.
        claimed_solutions: comma separated, and copied from THE USER'S
            QUESTION — every value the user asserts is a solution, and no
            others. Do not add solutions you believe are missing, and do not
            remove ones you believe are wrong. This check exists to compare
            the user's set against the true set; changing it verifies a
            different claim and produces a confident wrong answer.
            Write the imaginary unit as capital I; a lowercase i is read as
            an ordinary variable and the check will be refused.
        """
        return log.record(
            "solve_equation",
            claim,
            VerificationRequest(
                kind=VerificationKind.SOLUTION,
                lhs=lhs,
                rhs=rhs,
                variable=variable,
                candidate=claimed_solutions,
            ),
        )

    def check_limit(
        claim: str, expression: str, variable: str, point: str, claimed_value: str
    ) -> str:
        """Check the limit of an expression as a variable approaches a point.

        claim: the claim from the user's question that this check is testing.
        expression: the function, in SymPy syntax.
        variable: the variable that is approaching something.
        point: what it approaches. Use oo for infinity, -oo for negative
            infinity, otherwise the value from the user's question.
        claimed_value: the limit the user's question says it equals.
        """
        return log.record(
            "check_limit",
            claim,
            VerificationRequest(
                kind=VerificationKind.LIMIT,
                lhs=expression,
                rhs=claimed_value,
                variable=variable,
                point=point,
            ),
        )

    return [
        check_equality,
        check_numeric,
        check_primality,
        solve_equation,
        check_limit,
    ]
