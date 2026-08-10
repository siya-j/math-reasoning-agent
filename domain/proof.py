"""Proof artifacts (Design Doc Phase 6 / Phase 7 — theorem proving).

The verification pipeline produces a VERDICT. The proving pipeline produces
an ARTIFACT: Lean source that a compiler accepted. That difference matters —
a verdict asks you to trust the system, a proof can be rechecked by someone
who does not.

No framework code here, and no Lean code either. These types describe what a
proof attempt IS, not how it is produced or checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from domain.verdict import Verdict, VerificationStatus


class ProofStage(str, Enum):
    """Which part of the strategy produced an attempt (Prover Agent, §3)."""

    DIRECT = "direct"          # straight attempt, informally guided
    REFINE = "refine"          # revised using the compiler's errors
    SYNTHESIS = "synthesis"    # assembled from lemmas that were proved


@dataclass(frozen=True)
class ProofAttempt:
    number: int
    stage: ProofStage
    proof: str
    verdict: Verdict

    @property
    def succeeded(self) -> bool:
        return self.verdict.status is VerificationStatus.TRUE

    @property
    def error_count(self) -> int:
        """How badly this attempt failed, for choosing a draft to repair.

        Counted from the recorded verdict rather than tracked separately, so
        it stays honest even when the verifier is swapped out.
        """
        return self.verdict.detail.count("error:")


@dataclass
class Lemma:
    """An auxiliary fact generated to find a strategy, not to be trusted.

    A proved lemma may be used to build the final proof. That is safe only
    because the assembled proof is itself submitted to the compiler — the
    lemma is an INPUT to something checked, never evidence on its own.
    """

    informal: str
    statement: str = ""
    proof: str = ""
    verdict: Verdict | None = None

    @property
    def is_proved(self) -> bool:
        return (
            self.verdict is not None
            and self.verdict.status is VerificationStatus.TRUE
        )


@dataclass
class ProofRun:
    """Explicit state for one proof attempt (Principle 5)."""

    goal: str                                   # the question, in English
    statement: str = ""                         # the goal, formalised
    attempts: list[ProofAttempt] = field(default_factory=list)
    lemmas: list[Lemma] = field(default_factory=list)
    proof: str = ""                             # the accepted proof, if any
    verdict: Verdict | None = None
    trace: list[str] = field(default_factory=list)

    def log(self, event: str, detail: str = "") -> None:
        self.trace.append(f"{event}: {detail}" if detail else event)

    def record(self, attempt: ProofAttempt) -> None:
        self.attempts.append(attempt)
        self.log(attempt.stage.value, attempt.verdict.status.value)

    @property
    def proved(self) -> bool:
        return bool(self.proof) and self.verdict is not None and (
            self.verdict.status is VerificationStatus.TRUE
        )

    @property
    def proved_lemmas(self) -> list[Lemma]:
        return [lemma for lemma in self.lemmas if lemma.is_proved]

    def report(self) -> str:
        """A deterministic summary. The model contributes nothing to this.

        The formal statement is printed deliberately. The compiler guarantees
        the proof; only a human can confirm the statement says what was asked.
        """
        head = "[PROVED]" if self.proved else "[NOT PROVED]"
        lines = [f"{head} {self.goal}"]
        if self.statement:
            lines.append(f"  formal statement: {self.statement}")
        lines.append(f"  attempts: {len(self.attempts)}")

        if self.lemmas:
            proved = len(self.proved_lemmas)
            lines.append(f"  auxiliary lemmas: {proved}/{len(self.lemmas)} proved")
            for lemma in self.lemmas:
                mark = "proved" if lemma.is_proved else "unproved"
                lines.append(f"    [{mark}] {lemma.informal}")

        if self.proved:
            lines.append("  proof accepted by the compiler:")
            lines.extend(f"    {line}" for line in self.proof.splitlines())
        elif self.verdict is not None:
            lines.append(f"  {self.verdict.detail}")
        return "\n".join(lines)
