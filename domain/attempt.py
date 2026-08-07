"""One pass of the agent, and what came of it (Principle 3, Principle 5).

The pipeline — not the model — decides whether another attempt happens.
Recording every attempt, including the ones that failed, is what makes the
loop inspectable rather than emergent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from domain.check import Check
from domain.verdict import Verdict


class Strategy(str, Enum):
    """Why this attempt was made."""

    INITIAL = "initial"                    # first pass
    RETRY_MALFORMED = "retry-malformed"    # verifier could not decide; fix the check
    RETRY_NO_TOOLS = "retry-no-tools"      # nothing was verified; nudge once
    DECOMPOSE = "decompose"                # gather auxiliary evidence only


@dataclass(frozen=True)
class Attempt:
    """One agent invocation: the checks it ran and the verdict they produced."""

    number: int
    strategy: Strategy
    checks: list[Check] = field(default_factory=list)
    verdict: Verdict | None = None

    def summary(self) -> str:
        status = self.verdict.status.value if self.verdict else "n/a"
        return (
            f"#{self.number} ({self.strategy.value}): "
            f"{len(self.checks)} check(s) -> {status}"
        )
