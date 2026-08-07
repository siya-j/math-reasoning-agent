"""Faithfulness lint (Design Doc s14 — autoformalization quality).

The guard can prove that a check PASSED. It cannot prove the check was a
translation of the question the user asked. That gap produced a real
failure: asked "is 2 the only solution of x^2 = 4?", the agent checked
"are the solutions 2 and -2?", SymPy correctly said yes, and the system
confidently answered the wrong question.

This module is a partial, deterministic defence. It compares the numbers in
the formal check against the numbers in the question. A value that appears
in the check but nowhere in the question was invented by the model rather
than taken from the user.

Deliberately narrow. It cannot detect semantic drift that preserves the
numbers, and it does not try. It catches one specific, observed, damaging
mistake, using arithmetic rather than another language model.
"""

from __future__ import annotations

import re

from domain.verification import VerificationKind, VerificationRequest

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    """Numeric literals in a string, normalised so 2.0 and 2 compare equal."""
    found = set()
    for raw in _NUMBER.findall(text or ""):
        value = float(raw)
        found.add(str(int(value)) if value.is_integer() else str(value))
        # "2 and -2" written as "2, -2" vs "-2": keep the magnitude too, so a
        # minus sign supplied by the question's prose still counts.
        found.add(str(abs(int(value))) if value.is_integer() else str(abs(value)))
    return found


def unsupported_numbers(question: str, request: VerificationRequest) -> list[str]:
    """Numbers in the claimed solution set that never appear in the question.

    Only the `candidate` field is linted. It is the one field whose contents
    must come verbatim from the user's claim; every other field legitimately
    contains values the model derived (exponents, rearrangements, and so on).
    Linting those produced false positives in testing.
    """
    if request.kind is not VerificationKind.SOLUTION or not request.candidate:
        return []

    asked = _numbers(question)
    claimed = _NUMBER.findall(request.candidate)

    unsupported = []
    for raw in claimed:
        value = float(raw)
        key = str(int(value)) if value.is_integer() else str(value)
        if key not in asked:
            unsupported.append(raw.strip())
    return unsupported


def is_faithful(question: str, request: VerificationRequest) -> bool:
    return not unsupported_numbers(question, request)
