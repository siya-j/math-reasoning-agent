"""Verifier registry — deterministic correctness (Design Doc section 9).

Adding a verifier means adding one entry to VERIFIERS. The pipeline does
not change. Phase 6 adds LeanVerifier() here for abstract mathematics.
"""

from __future__ import annotations

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationRequest
from verifiers.base import Verifier
from verifiers.lean_verifier import LeanVerifier
from verifiers.sympy_verifier import SymPyVerifier

# Order matters: the first verifier that supports a request handles it.
# Adding Lean is one line. Principle 8 in practice — the pipeline, the guard
# and the reflection loop are all untouched by this change.
VERIFIERS: list[Verifier] = [SymPyVerifier(), LeanVerifier()]

NOT_APPLICABLE = Verdict(
    status=VerificationStatus.NOT_APPLICABLE,
    method="none",
    detail=(
        "No deterministic verifier can decide this claim yet. Computer algebra "
        "handles computational mathematics; claims in abstract algebra, "
        "topology, analysis or set theory need a proof assistant (Phase 6)."
    ),
)


def verify(request: VerificationRequest) -> Verdict:
    """Route a request to the first verifier that supports it."""
    for verifier in VERIFIERS:
        if verifier.supports(request):
            return verifier.verify(request)
    return NOT_APPLICABLE


__all__ = [
    "verify",
    "VERIFIERS",
    "Verifier",
    "SymPyVerifier",
    "LeanVerifier",
    "NOT_APPLICABLE",
]
