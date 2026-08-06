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
        "Eq Matrix Poly floor ceiling Mod gcd lcm isprime primerange"
    ).split()
    if hasattr(sympy, name)
}


def _parse(text: str):
    """Parse an expression string with a restricted namespace."""
    if not text.strip():
        raise ValueError("empty expression")
    return parse_expr(
        text,
        local_dict=dict(_ALLOWED),
        global_dict={},
        transformations=standard_transformations,
        evaluate=True,
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
        same_count = len(solutions) == len(claimed)
        all_matched = all(
            any(sympy.simplify(found - c) == 0 for c in claimed) for found in solutions
        )
        if same_count and all_matched:
            return self._true(f"Solutions confirmed: {solutions}.")
        return self._false(
            f"Claimed {claimed}, but SymPy found {solutions}."
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
