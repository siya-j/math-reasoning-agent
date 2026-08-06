"""What a deterministic verifier is allowed to say."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VerificationStatus(str, Enum):
    """A verifier may only ever return one of these.

    UNKNOWN and NOT_APPLICABLE exist on purpose: a verifier that guesses
    when it cannot decide has destroyed the only thing that made it useful.
    """

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"            # verifier applied, could not decide
    NOT_APPLICABLE = "n/a"         # no verifier exists for this claim


@dataclass(frozen=True)
class Verdict:
    """The result of deterministic verification.

    status: the decision.
    method: HOW it was decided ("trial division", later "sympy", "lean").
    detail: human-readable evidence, shown to the user.
    """

    status: VerificationStatus
    method: str
    detail: str

    @property
    def was_verified(self) -> bool:
        """True only if a deterministic system actually decided something."""
        return self.status in (VerificationStatus.TRUE, VerificationStatus.FALSE)
