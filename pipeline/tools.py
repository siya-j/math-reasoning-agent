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

    def check_series(
        claim: str, expression: str, variable: str, point: str, order: str, claimed: str
    ) -> str:
        """Check a Taylor or Maclaurin expansion of an expression.

        claim: the claim from the user's question that this check is testing.
        expression: the function being expanded, in SymPy syntax.
        variable: the variable of expansion.
        point: the centre of the expansion; use 0 for a Maclaurin series.
        order: how many terms to keep, as a string of digits. Count the terms
            in the user's claimed expansion and use one more than the highest
            power that appears.
        claimed: the expansion the user's question states, without any
            remainder or big-O term.
        """
        return log.record(
            "check_series",
            claim,
            VerificationRequest(
                kind=VerificationKind.SERIES,
                lhs=expression,
                rhs=claimed,
                variable=variable,
                point=point,
                order=order,
            ),
        )

    def check_matrix(claim: str, lhs: str, rhs: str) -> str:
        """Check whether two matrix expressions are equal.

        Write matrices as Matrix([[row], [row]]). Products, sums, powers,
        transpose, eye and zeros are available.

        Use this only when both sides are matrices. For a scalar result such
        as a determinant, use the numeric check instead.

        claim: the claim from the user's question that this check is testing.
        lhs, rhs: the two matrix expressions.
        """
        return log.record(
            "check_matrix",
            claim,
            VerificationRequest(kind=VerificationKind.MATRIX, lhs=lhs, rhs=rhs),
        )

    def check_inequality(
        claim: str, lhs: str, relation: str, rhs: str, variable: str
    ) -> str:
        """Check whether an inequality holds for EVERY real value of a variable.

        relation must be one of: <  <=  >  >=

        This asks whether the inequality is always true, not whether it is
        true somewhere. A single counterexample makes it false, and the check
        will report where it fails.

        claim: the claim from the user's question that this check is testing.
        lhs, rhs: the two sides, in SymPy syntax.
        variable: the variable quantified over. Only one variable is supported.
        """
        return log.record(
            "check_inequality",
            claim,
            VerificationRequest(
                kind=VerificationKind.INEQUALITY,
                lhs=lhs,
                rhs=rhs,
                relation=relation,
                variable=variable,
            ),
        )

    def check_factorization(claim: str, number: str, factorization: str) -> str:
        """Check whether a product is the PRIME factorisation of an integer.

        Two things are verified: that the factors multiply to the number, and
        that every factor is prime. A product of composite numbers that
        reaches the right total is reported as false.

        claim: the claim from the user's question that this check is testing.
        number: the integer being factorised.
        factorization: the product from the user's question, using ** for
            repeated factors.
        """
        return log.record(
            "check_factorization",
            claim,
            VerificationRequest(
                kind=VerificationKind.FACTORIZATION,
                lhs=number,
                rhs=factorization,
            ),
        )

    def check_dimensions(claim: str, lhs: str, rhs: str) -> str:
        """Check whether two physical quantities have the same DIMENSIONS.

        This asks whether they are the same KIND of thing (an energy, a
        force, a speed), not whether they are equal. Use it to test whether a
        formula was assembled correctly, and for any claim about what a unit
        means.

        Do not use this when neither side has a unit; that is arithmetic.

        claim: the claim from the user's question that this check is testing.
        lhs, rhs: the two quantities. Write units by their full singular name
            (meter, second, kilogram, joule, newton, pascal, mole, liter,
            volt, ohm, watt), with ** for powers and explicit multiplication.
            A bare number with no unit is dimensionless.
        """
        return log.record(
            "check_dimensions",
            claim,
            VerificationRequest(kind=VerificationKind.DIMENSION, lhs=lhs, rhs=rhs),
        )

    def check_quantity(
        claim: str, expression: str, expected: str, decimal_places: str = ""
    ) -> str:
        """Check whether a physical calculation equals a stated value WITH ITS UNIT.

        Use this for any question whose answer carries a unit. The unit is
        part of the answer: a distance reported in seconds is wrong however
        right its number is, and this check is the only one that can see that.

        The two sides may use different units for the same dimension; the
        conversion is done before comparing.

        claim: the claim from the user's question that this check is testing.
        expression: the calculation, with a unit on every physical quantity,
            built from the values the user's question states.
        expected: the value the user's question claims, with its unit.
        decimal_places: pass this ONLY when the question says the answer is
            rounded or given to a number of decimal places, as a string of
            digits. Leave it empty otherwise, which compares exactly.
        """
        return log.record(
            "check_quantity",
            claim,
            VerificationRequest(
                kind=VerificationKind.QUANTITY,
                lhs=expression,
                rhs=expected,
                tolerance=decimal_places,
            ),
        )

    def check_molar_mass(claim: str, formula: str, claimed_mass: str) -> str:
        """Check the molar mass of a chemical formula, in grams per mole.

        Never work a molar mass out yourself; always call this. The claim is
        judged at the precision it is written to, so a value rounded the way
        the question rounds it is accepted.

        claim: the claim from the user's question that this check is testing.
        formula: the compound, written the way chemists write it. Element
            symbols are case sensitive. Brackets are understood, and a dot
            before water of crystallisation.
        claimed_mass: the molar mass the user's question states, as a plain
            number with no unit and no arithmetic in it.
        """
        return log.record(
            "check_molar_mass",
            claim,
            VerificationRequest(
                kind=VerificationKind.MOLAR_MASS, lhs=formula, rhs=claimed_mass
            ),
        )

    def check_constant(claim: str, name: str, claimed_value: str) -> str:
        """Check a physical constant against its accepted value.

        Never state a constant from memory; always call this. The claim is
        judged at the precision it is written to, so a rounded value is
        accepted when its digits are right.

        Names are case sensitive where physics is: G is the gravitational
        constant and g is the acceleration due to gravity.

        claim: the claim from the user's question that this check is testing.
        name: the constant, by name or by its usual symbol.
        claimed_value: the value the user's question states, as a plain
            number in SI units, with no unit attached and no arithmetic.
        """
        return log.record(
            "check_constant",
            claim,
            VerificationRequest(
                kind=VerificationKind.CONSTANT, lhs=name, rhs=claimed_value
            ),
        )

    def check_possible(claim: str, quantity: str, value: str) -> str:
        """Check whether a value is PHYSICALLY IMPOSSIBLE for a quantity.

        This check can only ever refute. A value inside the possible range is
        reported as undecided, because being possible is not being correct.
        Use it as a sanity check on an answer you have computed, especially
        one produced by a formula you are unsure you assembled correctly.

        It catches what arithmetic checking cannot: a ratio inverted, a sign
        dropped, a quantity subtracted the wrong way round.

        claim: the claim from the user's question that this check is testing.
        quantity: what the number MEANS, in words, such as a probability, an
            efficiency, a concentration, an absolute temperature, a speed, a
            percentage yield, a mole fraction or a count.
        value: the number, in the unit that quantity is normally given in,
            as a plain number with no unit and no arithmetic.
        """
        return log.record(
            "check_possible",
            claim,
            VerificationRequest(
                kind=VerificationKind.PLAUSIBILITY, lhs=quantity, rhs=value
            ),
        )

    def check_equation_balances(claim: str, equation: str) -> str:
        """Check whether a chemical equation has the same atoms on both sides.

        Never count atoms yourself; always call this. An unbalanced equation
        invalidates every quantity computed from it, and miscounting is easy
        to do confidently.

        Balance is necessary but not sufficient: a balanced equation may
        still describe a reaction that does not occur.

        claim: the claim from the user's question that this check is testing.
        equation: the full equation with an arrow written as ->, species
            separated by +, and stoichiometric coefficients in front of the
            formulae they apply to.
        """
        return log.record(
            "check_equation_balances",
            claim,
            VerificationRequest(kind=VerificationKind.BALANCE, lhs=equation),
        )

    def check_statistic(
        claim: str, statistic: str, parameters: str, claimed_value: str
    ) -> str:
        """Check a probability or a statistical test against a claimed value.

        Use for genetics crosses, sampling questions, and goodness-of-fit
        tests on observed against expected counts.

        statistic: one of
            binomial probability   P(X = k)
            binomial at most       P(X <= k)
            binomial at least      P(X >= k)
            normal below           P(X <= x)
            normal above           P(X >= x)
            chi square statistic   the test statistic itself
            chi square p value     the p-value of a goodness-of-fit test
            t test p value         the two-sided p-value for a given t and df

        parameters: name=value pairs separated by commas. Binomial takes
            n, p and k; normal takes mu, sigma and x; chi square takes
            observed and expected as bracketed lists in the same category
            order; the t test takes t and df.

        claimed_value: the value the user's question states, as a plain
            number. It is judged at the precision it is written to.

        This reports the number and does not interpret it. Whether a p-value
        counts as significant is a judgement about the experiment.

        claim: the claim from the user's question that this check is testing.
        """
        return log.record(
            "check_statistic",
            claim,
            VerificationRequest(
                kind=VerificationKind.STATISTIC,
                lhs=statistic,
                rhs=claimed_value,
                parameters=parameters,
            ),
        )

    return [
        check_equality,
        check_numeric,
        check_primality,
        solve_equation,
        check_limit,
        check_series,
        check_matrix,
        check_inequality,
        check_factorization,
        check_dimensions,
        check_quantity,
        check_molar_mass,
        check_constant,
        check_possible,
        check_equation_balances,
        check_statistic,
    ]
