"""Explicit execution state (Principle 5).

The pipeline owns the control flow, so the state records not just what the
model said but what the SYSTEM decided to do and why: every attempt, every
check, every piece of auxiliary evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from domain.attempt import Attempt
from domain.check import Check
from domain.verdict import Verdict


@dataclass
class AgentRun:
    """Everything produced during one run."""

    question: str

    # Every agent invocation the pipeline made, in order (Phase 4).
    attempts: list[Attempt] = field(default_factory=list)

    # Auxiliary claims checked when the main claim could not be decided
    # (Phase 5). Evidence only: these NEVER change `verdict`.
    evidence: list[Check] = field(default_factory=list)

    # Computed by the guard from the deciding attempt's checks.
    verdict: Optional[Verdict] = None

    # The model's prose, prefixed by a deterministic verification banner.
    answer: str = ""

    trace: list[str] = field(default_factory=list)

    @property
    def checks(self) -> list[Check]:
        """Checks from the deciding (last non-decomposition) attempt."""
        return self.attempts[-1].checks if self.attempts else []

    @property
    def all_checks(self) -> list[Check]:
        """Every check across every attempt, for inspection."""
        return [c for attempt in self.attempts for c in attempt.checks]

    def log(self, step: str, detail: str = "") -> None:
        self.trace.append(f"{step}: {detail}" if detail else step)

    def record(self, attempt: Attempt) -> None:
        self.attempts.append(attempt)
        self.verdict = attempt.verdict
        self.log("attempt", attempt.summary())
