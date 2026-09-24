"""Chemistry verifier — does a reaction equation balance? (Phase 3)

Unlike the plausibility check next door, this one is SYMMETRIC and may
return TRUE. Balancing is not a matter of degree: every element either has
the same number of atoms on each side or it does not, and both answers are
decidable by counting.

Counting is also exactly what a language model is worst at and most
confident about. An unbalanced equation is the most common error in a
stoichiometry answer, and it invalidates every number computed after it, so
it is worth deciding deterministically.

WHAT IT DOES NOT DO. It does not decide whether a reaction occurs, whether
the products are the right products, or whether the equation describes
chemistry at all. `2H2 + O2 -> 2H2O` balances and so does an equation for a
reaction that never happens. Balance is a necessary condition, never a
sufficient one, and the verdict says so.
"""

from __future__ import annotations

import re

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from science.elements import FormulaError, parse_formula
from verifiers.base import Verifier

_SUPPORTED = {VerificationKind.BALANCE}

_ARROWS = ("<->", "-->", "->", "=>", "→", "⇌", "↔", "=")

_TERM = re.compile(r"^\s*(\d*)\s*(.+?)\s*$")


def _side(text: str) -> dict[str, int]:
    """Total atoms on one side of an equation, coefficients included."""
    totals: dict[str, int] = {}
    pieces = [piece for piece in text.split("+") if piece.strip()]
    if not pieces:
        raise FormulaError("a side of the equation is empty")
    for piece in pieces:
        match = _TERM.match(piece)
        coefficient = int(match.group(1)) if match.group(1) else 1
        if coefficient == 0:
            raise FormulaError(f"a coefficient of zero in '{piece.strip()}'")
        for element, count in parse_formula(match.group(2)).items():
            totals[element] = totals.get(element, 0) + coefficient * count
    return totals


def _split(equation: str) -> tuple[str, str]:
    for arrow in _ARROWS:
        if arrow in equation:
            left, _, right = equation.partition(arrow)
            return left, right
    raise FormulaError(
        "no reaction arrow found; write the equation with -> between "
        "reactants and products"
    )


class ChemistryVerifier(Verifier):
    name = "chemistry"

    def supports(self, request: VerificationRequest) -> bool:
        return request.kind in _SUPPORTED

    def verify(self, request: VerificationRequest) -> Verdict:
        try:
            return self._balance(request)
        except FormulaError as exc:
            return self._unknown(f"Not a readable chemical equation: {exc}")
        except Exception as exc:  # never crash the pipeline
            return self._unknown(
                f"The chemistry verifier could not process this: {exc}"
            )

    def _balance(self, request: VerificationRequest) -> Verdict:
        left_text, right_text = _split(request.lhs)
        left, right = _side(left_text), _side(right_text)

        mismatched = []
        for element in sorted(set(left) | set(right)):
            before, after = left.get(element, 0), right.get(element, 0)
            if before != after:
                mismatched.append(f"{element}: {before} left, {after} right")

        if not mismatched:
            counts = ", ".join(f"{e}x{left[e]}" for e in sorted(left))
            return self._true(
                f"Balanced: {counts} on both sides. (Balance is necessary for "
                "a correct equation, not sufficient — it says nothing about "
                "whether the reaction occurs or the products are right.)"
            )
        return self._false(
            "Not balanced. " + "; ".join(mismatched) + "."
        )

    # ------------------------------------------------------------- helpers
    def _true(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.TRUE, self.name, detail)

    def _false(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.FALSE, self.name, detail)

    def _unknown(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.UNKNOWN, self.name, detail)
