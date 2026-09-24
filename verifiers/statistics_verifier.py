"""Statistics verifier — distributions and tests (Phase 4).

WHY BIOLOGY NEEDS THIS. A biology numerical answer is usually not an
identity to be proved but a probability to be computed: the chance of a
genotype from a cross, whether an observed ratio departs from the expected
one by more than sampling explains, how a count sits against a distribution.
None of that is reachable by computer algebra or by a proof assistant, and
all of it is exactly computable.

IT USES sympy.stats, NOT scipy. scipy is not a dependency of this project
and adding one to reach standard distributions would be a poor trade;
sympy.stats is already present, gives exact rational answers for the
discrete cases, and returns in milliseconds at the sizes questions use.

WHAT IT REFUSES. A test whose parameters do not describe a distribution — a
negative count, probabilities outside [0, 1], expected frequencies that do
not sum to the observed total — is refused rather than computed. A p-value
from malformed input is a number with no meaning, and a number with no
meaning is the most dangerous thing this system can produce, because it
looks exactly like one with meaning.

IT DOES NOT INTERPRET. It will report that p = 0.04, and it will not tell
you the result is significant. Where the threshold sits is a judgement about
the experiment, not a deterministic fact, and the guard must not be handed
judgements dressed as verdicts.
"""

from __future__ import annotations

import re

import sympy
from sympy.stats import Binomial, ChiSquared, Normal, StudentT, cdf, density

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from science.precision import agrees, as_number
from verifiers.base import Verifier

_SUPPORTED = {VerificationKind.STATISTIC}

_LIST = re.compile(r"^\s*\[?([-+0-9eE.,\s]+)\]?\s*$")


class _Malformed(Exception):
    """Parameters that do not describe a distribution."""


def parse_parameters(text: str) -> dict[str, object]:
    """Read 'n=10, p=0.5, k=5' or 'observed=[10,20], expected=[15,15]'.

    Values are either a single number or a list of numbers. Anything else is
    malformed, and malformed is refused rather than repaired.
    """
    found: dict[str, object] = {}
    # Split on commas that are not inside brackets.
    depth, current, pieces = 0, "", []
    for character in text or "":
        if character in "[(":
            depth += 1
        elif character in "])":
            depth -= 1
        if character == "," and depth == 0:
            pieces.append(current)
            current = ""
        else:
            current += character
    pieces.append(current)

    for piece in pieces:
        if not piece.strip():
            continue
        if "=" not in piece:
            raise _Malformed(f"'{piece.strip()}' is not a name=value pair")
        name, _, value = piece.partition("=")
        name, value = name.strip().lower(), value.strip()
        if value.startswith("["):
            match = _LIST.match(value)
            if not match:
                raise _Malformed(f"'{value}' is not a list of numbers")
            numbers = [
                as_number(part) for part in match.group(1).split(",") if part.strip()
            ]
            if not numbers or any(number is None for number in numbers):
                raise _Malformed(f"'{value}' is not a list of numbers")
            found[name] = numbers
        else:
            number = as_number(value)
            if number is None:
                raise _Malformed(f"'{value}' is not a number")
            found[name] = number
    return found


def _require(parameters: dict, *names: str) -> list:
    missing = [name for name in names if name not in parameters]
    if missing:
        raise _Malformed(
            "missing " + ", ".join(missing)
            + f" (given: {', '.join(sorted(parameters)) or 'nothing'})"
        )
    return [parameters[name] for name in names]


def _whole(value, name: str) -> int:
    if value != int(value):
        raise _Malformed(f"{name} must be a whole number, got {value}")
    return int(value)


def _probability(value, name: str) -> float:
    if not 0.0 <= value <= 1.0:
        raise _Malformed(f"{name} must lie between 0 and 1, got {value}")
    return float(value)


# --------------------------------------------------------------- statistics
def _binomial(parameters: dict, comparison: str) -> tuple[float, str]:
    n, p, k = _require(parameters, "n", "p", "k")
    n, k = _whole(n, "n"), _whole(k, "k")
    p = _probability(p, "p")
    if n < 0:
        raise _Malformed(f"n must not be negative, got {n}")
    if not 0 <= k <= n:
        raise _Malformed(f"k must lie between 0 and n, got k={k} with n={n}")

    weights = density(Binomial("X", n, sympy.Rational(str(p)))).dict
    if comparison == "exactly":
        total = weights.get(k, sympy.Integer(0))
        words = f"P(X = {k})"
    elif comparison == "at most":
        total = sum((weights.get(i, 0) for i in range(k + 1)), sympy.Integer(0))
        words = f"P(X <= {k})"
    else:
        total = sum((weights.get(i, 0) for i in range(k, n + 1)), sympy.Integer(0))
        words = f"P(X >= {k})"
    return float(total), (
        f"{words} for a binomial with n={n}, p={p} is {sympy.nsimplify(total)} "
        f"= {float(total)}"
    )


def _normal(parameters: dict, comparison: str) -> tuple[float, str]:
    mu, sigma, x = _require(parameters, "mu", "sigma", "x")
    if sigma <= 0:
        raise _Malformed(f"sigma must be positive, got {sigma}")
    below = float(cdf(Normal("N", sympy.Float(mu), sympy.Float(sigma)))(x).evalf())
    value = below if comparison == "below" else 1.0 - below
    words = "P(X <= x)" if comparison == "below" else "P(X >= x)"
    return value, (
        f"{words} for a normal with mean {mu} and standard deviation {sigma} "
        f"at x={x} is {value}"
    )


def _chi_square(parameters: dict, wanted: str) -> tuple[float, str]:
    observed, expected = _require(parameters, "observed", "expected")
    if not isinstance(observed, list) or not isinstance(expected, list):
        raise _Malformed("observed and expected must both be lists")
    if len(observed) != len(expected):
        raise _Malformed(
            f"observed has {len(observed)} categories and expected has "
            f"{len(expected)}; they must match"
        )
    if len(observed) < 2:
        raise _Malformed("a goodness-of-fit test needs at least two categories")
    if any(value < 0 for value in observed):
        raise _Malformed("observed counts must not be negative")
    if any(value <= 0 for value in expected):
        raise _Malformed("expected counts must be positive")

    # A goodness-of-fit test compares one total against itself split up. If
    # the totals differ the two are not the same experiment, and the
    # statistic computed from them means nothing.
    if abs(sum(observed) - sum(expected)) > 1e-6 * max(1.0, sum(observed)):
        raise _Malformed(
            f"observed totals {sum(observed)} but expected totals "
            f"{sum(expected)}; a goodness-of-fit test compares one total "
            "against the way it was predicted to divide"
        )

    statistic = sum(
        (o - e) ** 2 / e for o, e in zip(observed, expected)
    )
    degrees = len(observed) - 1
    if wanted == "statistic":
        return statistic, (
            f"chi-square = sum (observed - expected)^2 / expected = "
            f"{statistic} with {degrees} degrees of freedom"
        )
    p_value = float((1 - cdf(ChiSquared("C", degrees))(statistic)).evalf())
    return p_value, (
        f"chi-square = {statistic} with {degrees} degrees of freedom gives "
        f"p = {p_value}. (This is the probability of a departure at least "
        "this large by chance. Whether that counts as significant is a "
        "judgement about the experiment, not something this check decides.)"
    )


def _student_t(parameters: dict) -> tuple[float, str]:
    t, df = _require(parameters, "t", "df")
    df = _whole(df, "df")
    if df < 1:
        raise _Malformed(f"df must be at least 1, got {df}")
    below = float(cdf(StudentT("T", df))(abs(t)).evalf())
    p_value = 2.0 * (1.0 - below)
    return p_value, (
        f"two-sided p for t = {t} with {df} degrees of freedom is {p_value}. "
        "(Whether that counts as significant is a judgement about the "
        "experiment, not something this check decides.)"
    )


_STATISTICS = {
    "binomial probability": lambda p: _binomial(p, "exactly"),
    "binomial at most": lambda p: _binomial(p, "at most"),
    "binomial at least": lambda p: _binomial(p, "at least"),
    "normal below": lambda p: _normal(p, "below"),
    "normal above": lambda p: _normal(p, "above"),
    "chi square statistic": lambda p: _chi_square(p, "statistic"),
    "chi square p value": lambda p: _chi_square(p, "p"),
    "t test p value": _student_t,
}

_ALIASES = {
    "binomial": "binomial probability",
    "binomial exactly": "binomial probability",
    "binomial at least k": "binomial at least",
    "binomial at most k": "binomial at most",
    "normal probability": "normal below",
    "chi squared statistic": "chi square statistic",
    "chi-square statistic": "chi square statistic",
    "chi squared p value": "chi square p value",
    "chi-square p value": "chi square p value",
    "chi square p-value": "chi square p value",
    "goodness of fit": "chi square p value",
    "t test": "t test p value",
    "t test p-value": "t test p value",
}


def _key(text: str) -> str:
    return " ".join((text or "").strip().lower().replace("_", " ").split())


class StatisticsVerifier(Verifier):
    name = "statistics"

    def supports(self, request: VerificationRequest) -> bool:
        return request.kind in _SUPPORTED

    def verify(self, request: VerificationRequest) -> Verdict:
        try:
            return self._statistic(request)
        except _Malformed as exc:
            return self._unknown(
                f"These parameters do not describe a distribution: {exc}. "
                "Refusing to compute, because a number from malformed input "
                "looks exactly like one that means something."
            )
        except Exception as exc:  # never crash the pipeline
            return self._unknown(
                f"The statistics verifier could not process this: {exc}"
            )

    def _statistic(self, request: VerificationRequest) -> Verdict:
        name = _key(request.lhs)
        name = _ALIASES.get(name, name)
        compute = _STATISTICS.get(name)
        if compute is None:
            return self._unknown(
                f"'{request.lhs}' is not a statistic this verifier computes. "
                "Available: " + ", ".join(sorted(_STATISTICS)) + "."
            )

        if as_number(request.rhs) is None:
            return self._unknown(
                f"'{request.rhs}' is not a plain number, so there is no "
                "claimed value to compare with."
            )

        value, working = compute(parse_parameters(request.parameters))

        if agrees(value, request.rhs):
            return self._true(f"{working}, matching the claimed {request.rhs}.")
        return self._false(f"{working}, not {request.rhs}.")

    # ------------------------------------------------------------- helpers
    def _true(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.TRUE, self.name, detail)

    def _false(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.FALSE, self.name, detail)

    def _unknown(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.UNKNOWN, self.name, detail)
