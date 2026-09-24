"""Plausibility verifier — refutation by physical impossibility (Phase 3).

THIS VERIFIER IS ASYMMETRIC, and that asymmetry is the entire design.

    value outside its physical domain  ->  FALSE
    value inside its physical domain   ->  UNKNOWN

A negative concentration is wrong and can be called wrong without computing
anything. But a concentration of 3 mol/L is not CORRECT for being possible,
and returning TRUE would say it was. The guard treats any TRUE as a check
that passed; a verifier that confirmed every value it could not refute would
quietly promote plausible wrong answers to verified ones. So this one is
only ever allowed to refute.

WHAT IT IS FOR. It catches the error no amount of arithmetic checking finds:
a calculation done correctly from a formula assembled backwards. Invert a
ratio and the arithmetic is flawless and the answer is an efficiency of 1.4.
Nothing downstream of the arithmetic can see that. This can.

The bounds live in science/domains.py, and every one of them is a
consequence of physics rather than a description of what is usual. The file
also lists the bounds deliberately left out, with reasons. A bound that
merely describes the typical would refute unusual but correct answers, at
the full authority of a refutation.
"""

from __future__ import annotations

import re

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from science import domains
from verifiers.base import Verifier

_SUPPORTED = {VerificationKind.PLAUSIBILITY}

_NUMBER = re.compile(r"^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$")


class PlausibilityVerifier(Verifier):
    name = "plausibility"

    def supports(self, request: VerificationRequest) -> bool:
        return request.kind in _SUPPORTED

    def verify(self, request: VerificationRequest) -> Verdict:
        try:
            return self._plausibility(request)
        except Exception as exc:  # never crash the pipeline
            return self._unknown(
                f"The plausibility verifier could not process this: {exc}"
            )

    def _plausibility(self, request: VerificationRequest) -> Verdict:
        domain = domains.find(request.lhs)
        if domain is None:
            return self._unknown(
                f"'{request.lhs}' is not a quantity with a known physical "
                "bound, so nothing can be ruled impossible. Quantities with "
                "bounds: " + ", ".join(domains.known_quantities()) + "."
            )

        if not _NUMBER.match(request.rhs or ""):
            return self._unknown(
                f"'{request.rhs}' is not a plain number, so there is no value "
                "to test against the bound."
            )

        value = float(request.rhs.strip())
        reason = domains.violation(domain, value)
        if reason:
            return self._false(f"Physically impossible. {reason}")

        # Deliberately NOT true. See the module docstring: being possible is
        # not being correct, and the guard reads TRUE as a passed check.
        return self._unknown(
            f"{value} is a possible {domain.quantity}, so it cannot be ruled "
            "out on physical grounds. That is not the same as being correct: "
            "this check can refute a value but never confirm one."
        )

    # ------------------------------------------------------------- helpers
    def _false(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.FALSE, self.name, detail)

    def _unknown(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.UNKNOWN, self.name, detail)
