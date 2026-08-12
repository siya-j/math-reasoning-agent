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


# Which field, per kind, is a TRANSCRIPTION of the user's claim rather than
# something the model derived. Only these are linted. Fields like `lhs` hold
# the expression under test and legitimately contain values the model worked
# out; linting them produced false positives.
#
# Each entry was added in response to an observed substitution, not by
# guesswork:
#   SOLUTION.candidate  — "is 2 the only solution of x^2=4?" checked as "2, -2"
#   SERIES.rhs          — a deliberately wrong expansion was silently replaced
#                         with the correct one, turning a false claim true
#   FACTORIZATION.rhs   — same shape of risk: the claimed product is the user's
_CLAIMED_FIELD = {
    VerificationKind.SOLUTION: "candidate",
    VerificationKind.SERIES: "rhs",
    VerificationKind.FACTORIZATION: "rhs",
}


def unsupported_in(claimed_text: str, question: str) -> list[str]:
    """Numbers in a transcription of a claim that the question never states.

    Extracted so the proving path can reuse the SAME rule: a formal statement
    is a transcription of the user's claim exactly as `candidate` is, and a
    number appearing in it but nowhere in the question was invented by the
    model. One implementation, two callers — a second copy would drift.
    """
    if not claimed_text:
        return []

    asked = _numbers(question)
    unsupported = []
    for raw in _NUMBER.findall(claimed_text):
        value = float(raw)
        key = str(int(value)) if value.is_integer() else str(value)
        if key not in asked:
            unsupported.append(raw.strip())
    return unsupported


def unsupported_numbers(question: str, request: VerificationRequest) -> list[str]:
    """Numbers in the model's transcription of the claim that the question never states.

    A value present in the check but absent from the question was invented by
    the model. The most damaging form of that is silent correction: the user
    claims something false, the model checks the true version instead, and
    every component behaves correctly while the answer addresses a question
    nobody asked.
    """
    field = _CLAIMED_FIELD.get(request.kind)
    if field is None:
        return []
    return unsupported_in(getattr(request, field, ""), question)


def is_faithful(question: str, request: VerificationRequest) -> bool:
    return not unsupported_numbers(question, request)
