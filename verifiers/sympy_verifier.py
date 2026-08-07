"""SymPy verifier — deterministic symbolic computation (Phase 3).

SCOPE LIMIT, stated plainly: SymPy is a Computer Algebra System. It can
decide computational claims (derivatives, integrals, identities, primality,
equation solutions). It CANNOT decide claims in abstract algebra, topology,
functional analysis or set theory. For those this verifier reports
NOT_APPLICABLE, and Phase 6 (Lean) is the intended answer.
"""

from __future__ import annotations

import sympy
from sympy.parsing.sympy_parser import parse_expr, standard_transformations

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers.base import Verifier

_SUPPORTED = {
    VerificationKind.EQUALITY,
    VerificationKind.NUMERIC,
    VerificationKind.PRIMALITY,
    VerificationKind.SOLUTION,
    VerificationKind.LIMIT,
    VerificationKind.SERIES,
    VerificationKind.MATRIX,
    VerificationKind.INEQUALITY,
    VerificationKind.FACTORIZATION,
}

# SECURITY: the expression strings come from a language model, and SymPy's
# parser evaluates what it reads. We therefore hand it an explicit allow-list
# instead of the full Python namespace, so "__import__('os')" cannot resolve.
_ALLOWED = {
    name: getattr(sympy, name)
    for name in (
        "Symbol symbols Integer Rational Float Abs sqrt exp log ln sin cos tan "
        "asin acos atan sinh cosh tanh pi E oo I factorial binomial gamma "
        "diff integrate limit summation Sum Product simplify expand factor "
        "Eq Matrix Poly floor ceiling Mod gcd lcm isprime primerange "
        "series det eye zeros ones transpose Rational "
        # Needed only for evaluate=False parsing: SymPy's parser emits
        # explicit Mul/Add/Pow calls when it is told not to simplify.
        "Mul Add Pow"
    ).split()
    if hasattr(sympy, name)
}


def _parse(text: str, evaluate: bool = True):
    """Parse an expression string with a restricted namespace.

    `evaluate=False` preserves the written structure. SymPy folds
    2**3 * 3**2 * 5 into 360 on sight, which erases exactly the information
    a factorisation check needs to inspect.
    """
    if not text.strip():
        raise ValueError("empty expression")
    return parse_expr(
        text,
        local_dict=dict(_ALLOWED),
        global_dict={},
        transformations=standard_transformations,
        evaluate=evaluate,
    )


class SymPyVerifier(Verifier):
    name = "sympy"

    def supports(self, request: VerificationRequest) -> bool:
        return request.kind in _SUPPORTED

    def verify(self, request: VerificationRequest) -> Verdict:
        try:
            if request.kind is VerificationKind.PRIMALITY:
                return self._primality(request)
            if request.kind is VerificationKind.EQUALITY:
                return self._equality(request)
            if request.kind is VerificationKind.NUMERIC:
                return self._numeric(request)
            if request.kind is VerificationKind.SOLUTION:
                return self._solution(request)
            if request.kind is VerificationKind.LIMIT:
                return self._limit(request)
            if request.kind is VerificationKind.SERIES:
                return self._series(request)
            if request.kind is VerificationKind.MATRIX:
                return self._matrix(request)
            if request.kind is VerificationKind.INEQUALITY:
                return self._inequality(request)
            if request.kind is VerificationKind.FACTORIZATION:
                return self._factorization(request)
        except Exception as exc:  # never crash the pipeline
            return self._unknown(f"SymPy could not process this: {exc}")
        return self._unknown("Unsupported request kind.")

    # ------------------------------------------------------------------ kinds
    def _primality(self, request: VerificationRequest) -> Verdict:
        n = int(_parse(request.lhs))
        if sympy.isprime(n):
            return self._true(f"{n} is prime (SymPy isprime).")
        factors = sympy.factorint(n)
        return self._false(f"{n} is not prime. Factorization: {factors}.")

    def _equality(self, request: VerificationRequest) -> Verdict:
        lhs, rhs = _parse(request.lhs), _parse(request.rhs)
        difference = sympy.simplify(lhs - rhs)

        if difference == 0:
            return self._true(
                f"simplify({request.lhs} - {request.rhs}) = 0, so they are equal."
            )

        # A symbol appearing on only ONE side is a free parameter, not a
        # variable we are entitled to instantiate. Two real failures came
        # from ignoring this:
        #   integrate(2*x, x) vs x**2 + C  -> C is a constant of integration
        #   some_invented_name    vs oo    -> a meaningless symbol
        # In both cases the equality is not well posed, so we must not rule
        # on it. Refusing here is the difference between a limited verifier
        # and a wrong one.
        unmatched = lhs.free_symbols ^ rhs.free_symbols
        if unmatched:
            names = ", ".join(sorted(str(s) for s in unmatched))
            return self._unknown(
                f"Not a well-posed identity: {names} appears on only one side "
                "(an unbound constant, or an undefined symbol). Refusing to decide."
            )

        # simplify() failing to reach 0 does NOT prove inequality. Probe
        # numerically: a nonzero value is a genuine counterexample; a zero
        # value means we simply could not prove it either way.
        counterexample = self._find_counterexample(difference)
        if counterexample is not None:
            point, value = counterexample
            return self._false(
                f"Not equal. At {point} the difference is {value}, not 0."
            )
        return self._unknown(
            f"Could not prove or disprove. simplify() gave {difference}, "
            "which vanishes at every point tested."
        )

    def _numeric(self, request: VerificationRequest) -> Verdict:
        lhs, rhs = _parse(request.lhs), _parse(request.rhs)

        # A numeric claim must be about numbers. If either side is a symbol,
        # the agent has handed us something it invented rather than computed,
        # and "not equal" would be a meaningless verdict.
        if not lhs.is_number or not rhs.is_number:
            side = request.lhs if not lhs.is_number else request.rhs
            return self._unknown(
                f"'{side}' is not a number, so this is not a numeric claim. "
                "Refusing to decide."
            )

        if sympy.simplify(lhs - rhs) == 0:
            return self._true(f"{request.lhs} = {sympy.N(lhs)} equals {request.rhs}.")
        return self._false(
            f"{request.lhs} evaluates to {sympy.N(lhs)}, not {sympy.N(rhs)}."
        )

    def _solution(self, request: VerificationRequest) -> Verdict:
        var = sympy.Symbol(request.variable)
        equation = sympy.Eq(_parse(request.lhs), _parse(request.rhs or "0"))
        solutions = sympy.solve(equation, var)

        if not request.candidate:
            return self._unknown(
                f"No claimed solutions to check. SymPy solutions: {solutions}."
            )

        claimed = [_parse(part) for part in request.candidate.split(",") if part.strip()]

        # A claimed solution containing a symbol that appears nowhere in the
        # equation is not a value — it is a name the model invented. This bit
        # us for real: asked about the roots of x**2 + 1, the agent wrote
        # lowercase "i", which SymPy parses as an ordinary symbol rather than
        # the imaginary unit (capital I). Comparing them gave FALSE for a
        # true claim. Refuse instead.
        known = set(equation.free_symbols) | {var}
        invented = {s for c in claimed for s in c.free_symbols} - known
        if invented:
            names = ", ".join(sorted(str(s) for s in invented))
            return self._unknown(
                f"Claimed solutions mention {names}, which does not appear in "
                "the equation, so they are not concrete values. (SymPy writes "
                "the imaginary unit as capital I.) Refusing to decide."
            )

        same_count = len(solutions) == len(claimed)
        all_matched = all(
            any(sympy.simplify(found - c) == 0 for c in claimed) for found in solutions
        )
        if same_count and all_matched:
            return self._true(f"Solutions confirmed: {solutions}.")
        return self._false(
            f"Claimed {claimed}, but SymPy found {solutions}."
        )

    def _limit(self, request: VerificationRequest) -> Verdict:
        variable = sympy.Symbol(request.variable)
        expression = _parse(request.lhs)
        point = _parse(request.point or "0")
        claimed = _parse(request.rhs)

        result = sympy.limit(expression, variable, point)

        # SymPy signals "no single limit" with nan or an oscillation bound.
        if result.has(sympy.nan) or result.has(sympy.AccumBounds):
            return self._unknown(
                f"The limit of {request.lhs} as {request.variable} -> "
                f"{request.point} does not exist as a single value."
            )

        if sympy.simplify(result - claimed) == 0:
            return self._true(
                f"limit({request.lhs}, {request.variable} -> {request.point}) "
                f"= {result}, as claimed."
            )
        return self._false(
            f"limit({request.lhs}, {request.variable} -> {request.point}) "
            f"= {result}, not {request.rhs}."
        )

    def _series(self, request: VerificationRequest) -> Verdict:
        variable = sympy.Symbol(request.variable)
        expression = _parse(request.lhs)
        claimed = _parse(request.rhs)
        point = _parse(request.point or "0")
        order = int(request.order) if request.order.strip() else 6

        actual = sympy.series(expression, variable, point, order).removeO()
        if sympy.simplify(sympy.expand(actual - claimed)) == 0:
            return self._true(
                f"Expansion of {request.lhs} about {point} to order {order} "
                f"is {actual}, as claimed."
            )
        return self._false(
            f"Expansion is {actual}, not {request.rhs}."
        )

    def _matrix(self, request: VerificationRequest) -> Verdict:
        lhs, rhs = _parse(request.lhs), _parse(request.rhs)
        matrix_type = sympy.matrices.MatrixBase
        if not isinstance(lhs, matrix_type) or not isinstance(rhs, matrix_type):
            return self._unknown(
                "Both sides must be matrices for a matrix check. Use the "
                "numeric check for scalar results such as a determinant."
            )
        if lhs.shape != rhs.shape:
            return self._false(
                f"Shapes differ: {lhs.shape} versus {rhs.shape}."
            )
        if sympy.simplify(lhs - rhs).is_zero_matrix:
            return self._true(f"Matrices are equal: {lhs.tolist()}.")
        return self._false(f"{lhs.tolist()} is not {rhs.tolist()}.")

    _NEGATION = {">": "<=", ">=": "<", "<": ">=", "<=": ">"}

    def _inequality(self, request: VerificationRequest) -> Verdict:
        relation = request.relation.strip()
        if relation not in self._NEGATION:
            return self._unknown(
                f"Unsupported relation {relation!r}. Use <, <=, > or >=."
            )

        difference = sympy.simplify(_parse(request.lhs) - _parse(request.rhs or "0"))

        # No variable: it is just an arithmetic comparison.
        if not difference.free_symbols:
            holds = {
                ">": difference > 0, ">=": difference >= 0,
                "<": difference < 0, "<=": difference <= 0,
            }[relation]
            detail = f"{request.lhs} - {request.rhs or '0'} = {difference}."
            return self._true(detail) if bool(holds) else self._false(detail)

        variable = sympy.Symbol(request.variable)
        if difference.free_symbols != {variable}:
            names = ", ".join(sorted(str(s) for s in difference.free_symbols))
            return self._unknown(
                f"Only single-variable inequalities are supported; this one "
                f"involves {names}."
            )

        # An inequality holds for all real x exactly when its negation has no
        # real solution. Asking for counterexamples is decidable far more
        # often than asking SymPy to prove the statement outright.
        negated = sympy.Rel(difference, 0, self._NEGATION[relation])
        counterexamples = sympy.solveset(negated, variable, sympy.S.Reals)

        if isinstance(counterexamples, sympy.ConditionSet):
            return self._unknown(
                f"Could not determine whether {request.lhs} {relation} "
                f"{request.rhs or '0'} holds for all real {variable}."
            )
        if counterexamples == sympy.S.EmptySet:
            return self._true(
                f"Holds for every real {variable}: the negation has no solution."
            )
        return self._false(
            f"Fails for {variable} in {counterexamples}."
        )

    def _factorization(self, request: VerificationRequest) -> Verdict:
        number = int(_parse(request.lhs))
        # evaluate=False keeps the written product intact so the factors
        # themselves can be inspected, not just the value they multiply to.
        written = _parse(request.rhs, evaluate=False)

        if int(sympy.sympify(written)) != number:
            return self._false(
                f"{request.rhs} multiplies to {int(sympy.sympify(written))}, "
                f"not {number}."
            )

        bases = [factor.as_base_exp()[0] for factor in sympy.Mul.make_args(written)]
        composite = [
            b for b in bases
            if not (getattr(b, "is_Integer", False) and sympy.isprime(int(b)))
        ]
        if composite:
            names = ", ".join(str(b) for b in composite)
            return self._false(
                f"The product equals {number}, but {names} "
                f"{'is' if len(composite) == 1 else 'are'} not prime. "
                f"The prime factorisation is {sympy.factorint(number)}."
            )
        return self._true(
            f"{request.rhs} is the prime factorisation of {number}."
        )

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _find_counterexample(expression):
        """Look for a point where `expression` is provably nonzero."""
        free = sorted(expression.free_symbols, key=str)
        for probe in (sympy.Rational(1, 2), sympy.Integer(2), sympy.Rational(7, 3)):
            point = {symbol: probe for symbol in free}
            try:
                value = sympy.N(expression.subs(point))
            except Exception:
                continue
            if value.is_number and abs(value) > 1e-9:
                return point or "all points", value
        return None

    def _true(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.TRUE, self.name, detail)

    def _false(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.FALSE, self.name, detail)

    def _unknown(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.UNKNOWN, self.name, detail)
