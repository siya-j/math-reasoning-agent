"""Uncertainty verifier — does a result carry the error bar it claims?

WHAT IT DECIDES. Given a formula, the measurements that feed it, and a
claimed result, it propagates the uncertainty and compares. Both halves of
the claim are checked, and BOTH MUST HOLD:

    the central value        at the precision the claim was written to
    the uncertainty          likewise, when the claim states one

A right value with a wrong error bar is a wrong answer. It is also the more
dangerous of the two, because the value looks correct and the error bar is
what a reader uses to decide whether a difference means anything.

IT INVENTS NO THRESHOLD. It never rules that two numbers "agree within
uncertainty" at some chosen k. That is a judgement about an experiment, and
this codebase already refuses to make those — the statistics verifier
reports a p-value and declines to call it significant. Here the comparison
is against the precision the claim itself was written to, which is the
user's own standard rather than ours.

WHAT IT REPORTS EVEN WHEN IT REFUSES. The propagated value, the
per-variable contributions, and the caveats. A scientist asking this
question usually wants the number more than the verdict, and the detail
string is where the guard lets a number through honestly: it was computed,
not asserted.
"""

from __future__ import annotations

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from science.precision import round_to, significant_figures
from science.uncertainty import (
    UncertaintyError,
    parse_measurement,
    parse_measurements,
    propagate,
)
from verifiers.base import Verifier

_SUPPORTED = {VerificationKind.UNCERTAINTY}


def _agrees(computed: float, claimed_text: str) -> bool:
    """Compare at the precision the CLAIM was written to."""
    figures = significant_figures(claimed_text)
    try:
        claimed = float(claimed_text)
    except (TypeError, ValueError):
        return False
    return round_to(computed, figures) == round_to(claimed, figures)


def _claimed_parts(text: str) -> tuple[str, str]:
    """The claim split back into its written value and written uncertainty.

    The strings matter, not just the numbers: each side is compared at the
    precision IT was written to, and "0.5" and "0.50" are different claims
    about how well the uncertainty is known.
    """
    body = (text or "").strip()
    for marker in ("±", "+/-", "+-", "\\pm"):
        if marker in body:
            left, _, right = body.partition(marker)
            return left.strip(), right.strip()
    return body, ""


class UncertaintyVerifier(Verifier):
    name = "uncertainty"

    def supports(self, request: VerificationRequest) -> bool:
        return request.kind in _SUPPORTED

    def verify(self, request: VerificationRequest) -> Verdict:
        try:
            return self._uncertainty(request)
        except UncertaintyError as exc:
            return self._unknown(
                f"These inputs do not describe a measurement: {exc}. "
                "Refusing to propagate, because an error bar computed from "
                "malformed input looks exactly like one that means something."
            )
        except Exception as exc:  # never crash the pipeline
            return self._unknown(
                f"The uncertainty verifier could not process this: {exc}"
            )

    def _uncertainty(self, request: VerificationRequest) -> Verdict:
        measurements = parse_measurements(request.parameters)
        result = propagate(request.lhs, measurements)

        value_text, sigma_text = _claimed_parts(request.rhs)
        if not value_text:
            return self._unknown(
                f"No claimed result to check. {self._report(request, result)}"
            )
        # Reading it as a measurement first turns "nine" into a refusal here
        # rather than a silent False further down.
        parse_measurement(request.rhs)

        value_ok = _agrees(result.result.value, value_text)

        if not sigma_text:
            # A claim with no error bar is a claim about the value only. Say
            # so, and hand back the uncertainty that was never claimed.
            if value_ok:
                return self._true(
                    f"The value is right. {self._report(request, result)} "
                    "The claim states no uncertainty, so only the value was "
                    "checked."
                )
            return self._false(
                f"The value is wrong. {self._report(request, result)} "
                f"not {value_text}."
            )

        sigma_ok = _agrees(result.result.uncertainty, sigma_text)

        if value_ok and sigma_ok:
            return self._true(
                f"Value and uncertainty both confirmed. "
                f"{self._report(request, result)}"
            )
        if value_ok and not sigma_ok:
            return self._false(
                f"The value is right but the error bar is not: the propagated "
                f"uncertainty is {result.result.uncertainty:.6g}, not "
                f"{sigma_text}. {self._report(request, result)}"
            )
        return self._false(
            f"The value is wrong: {result.result.value:.6g}, not {value_text}. "
            f"{self._report(request, result)}"
        )

    # ------------------------------------------------------------- reporting
    @staticmethod
    def _report(request: VerificationRequest, result) -> str:
        """The number and its working, whatever the verdict.

        A propagated uncertainty with no terms shown is an assertion; with
        its per-variable contributions it is evidence, and the reader can
        see WHICH measurement dominates, which is usually the actionable
        part of the answer.
        """
        lines = [
            f"{request.lhs} = {result.result} "
            f"(first-order propagation: {result.working})."
        ]
        if result.caveats:
            lines.append("Assumes: " + "; ".join(result.caveats) + ".")
        return " ".join(lines)

    def _true(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.TRUE, self.name, detail)

    def _false(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.FALSE, self.name, detail)

    def _unknown(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.UNKNOWN, self.name, detail)
