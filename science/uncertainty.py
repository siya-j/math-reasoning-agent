"""Propagating measurement uncertainty through a formula.

WHY THIS IS THE GAP THAT MATTERS. A textbook number is exact; a measured one
is not. `g = 9.80665` is a definition, `9.81 ± 0.02` is a measurement, and
every quantity a working scientist computes carries an error bar. Propagating
those bars correctly is mechanical, error-prone, and the single most common
defect in published numerical work — which makes it exactly what a
deterministic verifier should own.

THE METHOD. First-order propagation, the standard GUM treatment: for
f(x_1 ... x_n) with standard uncertainties s_i,

    s_f^2 = SUM_i ( df/dx_i )^2 * s_i^2

evaluated at the measured point.

SYMBOLIC DIFFERENTIATION, NOT INTERVAL ARITHMETIC, and the difference is a
soundness matter rather than a stylistic one. Intervals treat every
occurrence of a variable as independent, so `x - x` comes out with an
uncertainty of s*sqrt(2) instead of zero, and a quantity that cancels
acquires an error bar it does not have. Differentiating the SYMBOLIC
expression gets this right: SymPy simplifies d(x - x)/dx to 0 before the
uncertainty is ever computed.

WHAT IT ASSUMES, all three stated in every verdict because a propagated
uncertainty is worthless without them:

  INDEPENDENCE   distinct variables are uncorrelated. Repeated occurrences
                 of the SAME variable are handled exactly; two different
                 variables measured on the same apparatus are not, and this
                 module cannot know that they were.
  LINEARITY      f is approximately linear across +/- s. False for a
                 quantity whose uncertainty is a large fraction of itself.
  STANDARD       the inputs are standard uncertainties (one sigma), not
                 tolerances, confidence intervals, or worst cases.

NO THRESHOLD IS INVENTED. This module never decides that two values "agree".
Where a comparison is needed the tolerance comes from the USER's own stated
uncertainty, exactly as decimal places come from a question that says how it
rounded. Choosing a k and declaring agreement at k sigma would be a judgement
about an experiment, and the statistics verifier already refuses to make
those.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sympy
from sympy.parsing.sympy_parser import parse_expr, standard_transformations

# Every way a person writes a plus-or-minus.
_PLUS_MINUS = ("±", "+/-", "+-", "\\pm")

_NAME = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")

# SECURITY: same reasoning as every other verifier here. These strings come
# from a language model and the parser evaluates what it reads.
_ALLOWED = {
    name: getattr(sympy, name)
    for name in (
        "sqrt exp log ln sin cos tan asin acos atan sinh cosh tanh "
        "pi E Abs Rational Integer Float floor ceiling Mul Add Pow"
    ).split()
    if hasattr(sympy, name)
}


class UncertaintyError(ValueError):
    """Input that does not describe a measurement."""


@dataclass(frozen=True)
class Measurement:
    value: float
    uncertainty: float = 0.0

    @property
    def is_exact(self) -> bool:
        return self.uncertainty == 0.0

    def __str__(self) -> str:
        return (f"{self.value}" if self.is_exact
                else f"{self.value} +/- {self.uncertainty}")

    @property
    def relative(self) -> float | None:
        if self.value == 0:
            return None
        return abs(self.uncertainty / self.value)


def parse_measurement(text: str) -> Measurement:
    """Read `9.81 +/- 0.02`, `9.81`, or `9.81 ± 0.02`."""
    body = (text or "").strip()
    if not body:
        raise UncertaintyError("empty measurement")

    for marker in _PLUS_MINUS:
        if marker in body:
            left, _, right = body.partition(marker)
            value, sigma = _number(left), _number(right)
            if sigma < 0:
                raise UncertaintyError(
                    f"an uncertainty cannot be negative, got {sigma}"
                )
            return Measurement(value, sigma)
    return Measurement(_number(body))


def _number(text: str) -> float:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        raise UncertaintyError(f"'{text.strip()}' is not a number") from None


def parse_measurements(text: str) -> dict[str, Measurement]:
    """Read `m = 2 +/- 0.1, v = 3 +/- 0.05` into named measurements."""
    found: dict[str, Measurement] = {}
    for piece in (text or "").split(","):
        if not piece.strip():
            continue
        if "=" not in piece:
            raise UncertaintyError(
                f"'{piece.strip()}' is not a name = value pair"
            )
        name, _, value = piece.partition("=")
        name = name.strip()
        if not _NAME.match(name):
            raise UncertaintyError(f"'{name}' is not a variable name")
        if name in found:
            raise UncertaintyError(f"'{name}' was given twice")
        found[name] = parse_measurement(value)
    if not found:
        raise UncertaintyError("no measurements given")
    return found


def _parse_formula(text: str, names: set[str]):
    if not (text or "").strip():
        raise UncertaintyError("empty formula")
    namespace = dict(_ALLOWED)
    namespace.update({name: sympy.Symbol(name) for name in names})
    try:
        expression = parse_expr(
            text, local_dict=namespace, global_dict={},
            transformations=standard_transformations, evaluate=True,
        )
    except Exception as exc:
        # The namespace deliberately omits `Symbol`, so an unknown name
        # cannot quietly become a free variable -- it fails here instead.
        # That strictness is wanted; only the exception TYPE was wrong, and
        # letting a NameError escape would make every caller handle SymPy's
        # internals rather than this module's declared contract.
        raise UncertaintyError(
            f"could not read the formula '{text.strip()}': every name in it "
            f"must be one of the measurements "
            f"({', '.join(sorted(names)) or 'none given'}) or a standard "
            f"function. ({type(exc).__name__}: {exc})"
        ) from None

    unknown = {str(s) for s in expression.free_symbols} - names
    if unknown:
        raise UncertaintyError(
            f"the formula uses {', '.join(sorted(unknown))}, which "
            "no measurement provides a value for"
        )
    return expression


@dataclass(frozen=True)
class Propagation:
    result: Measurement
    working: str
    caveats: tuple[str, ...]

    @property
    def contributions(self) -> str:
        return self.working


def propagate(formula: str, measurements: dict[str, Measurement]) -> Propagation:
    """The value of `formula`, with its uncertainty and the working.

    The working is returned because a propagated uncertainty with no terms
    shown is an assertion. With its per-variable contributions it is
    evidence, and a reader can see which measurement dominates -- which is
    usually the actionable part of the answer.
    """
    expression = _parse_formula(formula, set(measurements))
    point = {sympy.Symbol(n): sympy.Float(m.value)
             for n, m in measurements.items()}

    value = float(sympy.N(expression.subs(point)))

    variance = 0.0
    terms = []
    for name, measurement in sorted(measurements.items()):
        symbol = sympy.Symbol(name)
        # Symbolic FIRST: d(x - x)/dx is 0 before any number is substituted,
        # so a cancelled variable contributes nothing rather than sqrt(2)*s.
        derivative = sympy.diff(expression, symbol)
        slope = float(sympy.N(derivative.subs(point)))
        contribution = (slope * measurement.uncertainty) ** 2
        variance += contribution
        if measurement.uncertainty:
            terms.append(
                f"{name}: (d/d{name} = {slope:.6g}) x {measurement.uncertainty} "
                f"-> {abs(slope) * measurement.uncertainty:.6g}"
            )

    sigma = variance ** 0.5
    result = Measurement(value, sigma)

    caveats = _caveats(measurements, result)
    working = "; ".join(terms) if terms else "every input is exact"
    return Propagation(result, working, tuple(caveats))


def _caveats(measurements: dict[str, Measurement], result: Measurement) -> list[str]:
    """Say when the assumptions are visibly strained.

    Not a refusal: first-order propagation is still the standard answer.
    But a reader who is not told that a 40% relative uncertainty breaks the
    linearity assumption will read the number as more exact than it is.
    """
    notes: list[str] = []
    large = [name for name, m in sorted(measurements.items())
             if m.relative is not None and m.relative > 0.1]
    if large:
        notes.append(
            f"{', '.join(large)} carr{'y' if len(large) > 1 else 'ies'} a "
            "relative uncertainty above 10%, where the linear approximation "
            "starts to strain"
        )
    # Only when more than one input actually CARRIES an uncertainty. Warning
    # about correlation between exact values is noise, and a caveat a reader
    # learns to skip is worse than none.
    if sum(1 for m in measurements.values() if m.uncertainty) > 1:
        notes.append(
            "distinct variables are assumed uncorrelated; if they were "
            "measured together this underestimates or overestimates the result"
        )
    if result.relative is not None and result.relative > 0.5:
        notes.append(
            "the result's uncertainty exceeds half its value, so the central "
            "value carries little information"
        )
    return notes
