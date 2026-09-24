"""Verifier registry — deterministic correctness (Design Doc section 9).

Adding a verifier means adding one entry to VERIFIERS. The pipeline does
not change. Phase 6 adds LeanVerifier() here for abstract mathematics.
"""

from __future__ import annotations

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationRequest
from verifiers.base import Verifier
from verifiers.chemistry_verifier import ChemistryVerifier
from verifiers.lean_verifier import LeanVerifier
from verifiers.plausibility_verifier import PlausibilityVerifier
from verifiers.reference_verifier import ReferenceVerifier
from verifiers.statistics_verifier import StatisticsVerifier
from verifiers.sympy_verifier import SymPyVerifier
from verifiers.uncertainty_verifier import UncertaintyVerifier
from verifiers.units_verifier import UnitsVerifier

# Order matters: the first verifier that supports a request handles it.
# Adding Lean is one line. Principle 8 in practice — the pipeline, the guard
# and the reflection loop are all untouched by this change.
#
# The kinds are disjoint, so this order expresses no precedence between the
# science verifiers; it is the order they were built in. Lean stays last
# because it is the only one that costs seconds rather than milliseconds.
VERIFIERS: list[Verifier] = [
    SymPyVerifier(),
    UnitsVerifier(),
    ReferenceVerifier(),
    PlausibilityVerifier(),
    ChemistryVerifier(),
    StatisticsVerifier(),
    UncertaintyVerifier(),
    LeanVerifier(),
]

# Methods whose UNKNOWN means "I found nothing wrong" rather than "I could
# not decide". Derived from the verifiers themselves so that the guard never
# hardcodes a verifier's name.
REFUTATION_ONLY: frozenset[str] = frozenset(
    verifier.name for verifier in VERIFIERS if verifier.refutes_only
)

NOT_APPLICABLE = Verdict(
    status=VerificationStatus.NOT_APPLICABLE,
    method="none",
    detail=(
        "No deterministic verifier can decide this claim yet. Computer algebra "
        "handles computational mathematics; units, molar masses, constants, "
        "equation balancing and statistics handle the numerical sciences; "
        "claims in abstract algebra, topology, analysis or set theory need a "
        "proof assistant. An empirical fact — whether a reaction occurs, "
        "whether a dose is safe — is not a calculation and none of them apply."
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
    "UnitsVerifier",
    "ReferenceVerifier",
    "PlausibilityVerifier",
    "ChemistryVerifier",
    "StatisticsVerifier",
    "UncertaintyVerifier",
    "LeanVerifier",
    "NOT_APPLICABLE",
    "REFUTATION_ONLY",
]
