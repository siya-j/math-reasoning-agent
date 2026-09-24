"""Comparing numbers at the precision they were written to.

Shared by every science verifier that compares a computed value against a
claimed one. One implementation, several callers — a second copy would
drift, which is the same reasoning that put `unsupported_in` in one place in
pipeline/faithfulness.py.

THE RULE. A claim is judged at its own precision. The computed value is
rounded to as many significant figures as the claim was written with, and
then compared. Asked "is Avogadro's number 6.022e23?" the honest answer is
yes; digit-for-digit comparison against 6.02214076e23 would call it wrong.

The rule cannot be used to excuse a wrong digit: 6.023e23 is also four
significant figures, and still does not match.
"""

from __future__ import annotations

import math
import re

_NUMBER = re.compile(r"^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$")


def as_number(text: str) -> float | None:
    """Read a plain numeric literal, or None.

    Deliberately strict: a number and nothing else. These values come from a
    language model, and an expression here would mean the model is computing
    where it was asked to quote.
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
    strict reading: it can only refuse a claim that a lenient reading would
    confirm, and a question meaning otherwise can say so.
    """
    digits = (text or "").strip().lstrip("+-")
    digits = re.split(r"[eE]", digits)[0]
    if "." in digits:
        whole, _, fraction = digits.partition(".")
        stripped = (whole + fraction).lstrip("0")
        return len(stripped) if stripped else 1
    stripped = digits.lstrip("0")
    return len(stripped) if stripped else 1


def round_to(value: float, figures: int) -> float:
    """Round to a number of SIGNIFICANT FIGURES, not decimal places.

    sympy.Float(value, n) sets binary precision and is not this: it turned
    the speed of light at one significant figure into 297795584 rather
    than 3e8.
    """
    if value == 0 or figures <= 0:
        return 0.0
    exponent = math.floor(math.log10(abs(value)))
    return round(value, -(exponent - figures + 1))


def agrees(computed: float, claimed_text: str) -> bool:
    """Does a computed value match a claim, judged at the claim's precision?"""
    claimed = as_number(claimed_text)
    if claimed is None:
        return False
    figures = significant_figures(claimed_text)
    return round_to(computed, figures) == round_to(claimed, figures)
