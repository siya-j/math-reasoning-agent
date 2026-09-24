"""The verifier interface (Principles 2 and 8).

Every deterministic system plugs in here: SymPy today, Lean in Phase 6,
other provers later. The pipeline never learns their names.

A verifier answers exactly one question: "given this request, what is the
verdict?" It is always allowed to answer UNKNOWN. A verifier that guesses
when it cannot decide has destroyed the only thing that made it useful.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from domain.verdict import Verdict
from domain.verification import VerificationRequest


class Verifier(ABC):
    """Base class for all deterministic verifiers."""

    name: str = "unnamed"

    # Can this verifier ever CONFIRM, or only refute?
    #
    # A refutation-only verifier answers "that is impossible" or it answers
    # nothing. Its UNKNOWN therefore means "I found nothing wrong", which is
    # an ABSTENTION, not an inconclusive result — and the guard must not
    # average it in with checks that genuinely failed to decide. Treating
    # the two alike downgrades a correctly verified answer to unverified
    # every time a sanity check is performed, which punishes exactly the
    # behaviour the sanity check exists to encourage.
    refutes_only: bool = False

    @abstractmethod
    def supports(self, request: VerificationRequest) -> bool:
        """Can this verifier handle this kind of request?"""

    @abstractmethod
    def verify(self, request: VerificationRequest) -> Verdict:
        """Decide the request. Must never raise; return UNKNOWN instead."""
