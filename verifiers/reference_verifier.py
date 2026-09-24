"""Reference verifier — molar masses and physical constants (Phase 2).

These two kinds share a file because they are one activity: deciding a claim
against a table of looked-up values rather than by computing. Neither is
derivable, both are things a scientist reaches for a book to settle, and both
are decided the same way — by comparing at the precision the claim was
STATED TO.

THE PRECISION RULE. Asked "is Avogadro's number 6.022e23?", the honest answer
is yes: the claim is written to four significant figures and is correct to
four significant figures. Comparing it against 6.02214076e23 digit for digit
would return FALSE for a claim every chemist would mark right. So a claim is
judged at its own precision — the accepted value is rounded to as many
significant figures as the claim was written with, and then compared.

This cuts both ways, which is the point. It cannot be used to excuse a wrong
digit: 6.023e23 is still four figures, and still wrong.

MEASURED CONSTANTS ARE DIFFERENT. An exact constant can be decided to any
precision. A measured one cannot. If a claim about G is stated more finely
than the measurement itself resolves, the verifier returns UNKNOWN, because
FALSE would be asserting knowledge that nobody has.
"""

from __future__ import annotations

import math
import re

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from science import constants
from science.elements import FormulaError, molar_mass
from verifiers.base import Verifier

_SUPPORTED = {VerificationKind.MOLAR_MASS, VerificationKind.CONSTANT}

_NUMBER = re.compile(
    r"^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$"
)


def _as_number(text: str):
    """Read a plain numeric literal, or None.

    Deliberately strict: this accepts a number and nothing else. The values
    come from a language model, and an expression here would mean the model
    is computing where it should be quoting.
    """
    if not _NUMBER.match(text or ""):
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None


def significant_figures(text: str) -> int:
    """How many significant figures a numeric literal was written to.

    Leading zeros never count. Trailing zeros after a decimal point do. A
    trailing zero in a bare integer is counted as significant, which is the
    strict reading: it can only ever refuse a claim that a lenient reading
    would confirm, and a question that means otherwise can say so.
    """
    digits = (text or "").strip().lstrip("+-")
    digits = re.split(r"[eE]", digits)[0]
    if "." in digits:
        whole, _, fraction = digits.partition(".")
        stripped = (whole + fraction).lstrip("0")
        return len(stripped) if stripped else 1
    stripped = digits.lstrip("0")
    return len(stripped) if stripped else 1


def _round_to(value: float, figures: int) -> float:
    """Round to a number of SIGNIFICANT FIGURES, not decimal places.

    sympy.Float(value, n) sets binary precision and is not this: it turned
    the speed of light at one significant figure into 297795584 rather
    than 3e8.
    """
    if value == 0 or figures <= 0:
        return 0.0
    exponent = math.floor(math.log10(abs(value)))
    return round(value, -(exponent - figures + 1))


class ReferenceVerifier(Verifier):
    name = "reference"

    def supports(self, request: VerificationRequest) -> bool:
        return request.kind in _SUPPORTED

    def verify(self, request: VerificationRequest) -> Verdict:
        try:
            if request.kind is VerificationKind.MOLAR_MASS:
                return self._molar_mass(request)
            if request.kind is VerificationKind.CONSTANT:
                return self._constant(request)
        except FormulaError as exc:
            return self._unknown(f"Not a readable chemical formula: {exc}")
        except Exception as exc:  # never crash the pipeline
            return self._unknown(f"The reference verifier could not process this: {exc}")
        return self._unknown("Unsupported request kind.")

    # ------------------------------------------------------------------ kinds
    def _molar_mass(self, request: VerificationRequest) -> Verdict:
        claimed = _as_number(request.rhs)
        if claimed is None:
            return self._unknown(
                f"'{request.rhs}' is not a plain number, so there is no molar "
                "mass claim to check."
            )

        computed, working = molar_mass(request.lhs)
        figures = significant_figures(request.rhs)
        rounded = _round_to(computed, figures)

        if rounded == _round_to(claimed, figures):
            return self._true(
                f"The molar mass of {request.lhs} is {computed:.4f} g/mol "
                f"({working}), which is {rounded} to {figures} significant "
                f"figures, matching the claimed {claimed}."
            )
        return self._false(
            f"The molar mass of {request.lhs} is {computed:.4f} g/mol "
            f"({working}), not {claimed}."
        )

    def _constant(self, request: VerificationRequest) -> Verdict:
        constant = constants.find(request.lhs)
        if constant is None:
            return self._unknown(
                f"'{request.lhs}' is not in the constants table, so there is "
                "nothing to compare against. Known constants: "
                + ", ".join(constants.known_names())
                + "."
            )

        claimed = _as_number(request.rhs)
        if claimed is None:
            return self._unknown(
                f"'{request.rhs}' is not a plain number, so there is no value "
                "to compare with."
            )

        figures = significant_figures(request.rhs)

        # A measured constant is only known so far. A claim finer than the
        # measurement resolves cannot be ruled on either way.
        if not constant.is_exact:
            resolution = abs(constant.value) * 10.0 ** (1 - figures)
            if resolution < constant.uncertainty:
                return self._unknown(
                    f"The {constant.name} is measured, not defined: "
                    f"{constant.value} +/- {constant.uncertainty} "
                    f"{constant.unit}. The claim is stated to {figures} "
                    "significant figures, which is finer than the measurement "
                    "resolves, so it cannot be decided either way."
                )

        rounded = _round_to(constant.value, figures)
        basis = "exact by definition" if constant.is_exact else (
            f"measured, +/- {constant.uncertainty}"
        )
        if rounded == _round_to(claimed, figures):
            return self._true(
                f"The {constant.name} is {constant.value} {constant.unit} "
                f"({basis}), which is {rounded} to {figures} significant "
                f"figures, matching the claimed {claimed}."
            )
        return self._false(
            f"The {constant.name} is {constant.value} {constant.unit} "
            f"({basis}), which is {rounded} to {figures} significant figures, "
            f"not {claimed}."
        )

    # ------------------------------------------------------------- helpers
    def _true(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.TRUE, self.name, detail)

    def _false(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.FALSE, self.name, detail)

    def _unknown(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.UNKNOWN, self.name, detail)
