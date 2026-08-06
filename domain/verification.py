"""A machine-checkable question, extracted from a natural-language claim.

This is the bridge between the probabilistic half of the system and the
deterministic half. The LLM produces a VerificationRequest; verifiers
consume it. Neither side needs to know about the other.

This is the smallest possible form of autoformalization (Design Doc s14).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VerificationKind(str, Enum):
    """What kind of check a verifier is being asked to perform."""

    EQUALITY = "equality"    # is lhs == rhs for all values? (identities, derivatives)
    NUMERIC = "numeric"      # does lhs evaluate to rhs?
    PRIMALITY = "primality"  # is the integer in lhs prime?
    SOLUTION = "solution"    # are the solutions of lhs = rhs exactly `candidate`?
    NONE = "none"            # nothing here can be checked deterministically


@dataclass(frozen=True)
class VerificationRequest:
    """A claim rewritten as expressions a computer algebra system can parse.

    lhs/rhs/candidate hold SymPy-syntax strings, e.g. "diff(x**3, x)".
    They are STRINGS, not SymPy objects, so this module stays framework-free.
    """

    kind: VerificationKind
    lhs: str = ""
    rhs: str = ""
    variable: str = "x"
    candidate: str = ""       # comma-separated claimed solutions (SOLUTION only)

    @property
    def is_checkable(self) -> bool:
        return self.kind is not VerificationKind.NONE
