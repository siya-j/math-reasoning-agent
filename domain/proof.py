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

    CHEAP = "cheap"            # standard tactics and retrieved premises, no model
    DIRECT = "direct"          # straight attempt, informally guided
    REFINE = "refine"          # revised using the compiler's errors
    SKELETON = "skeleton"      # decomposed into `have` steps, holes filled
    SYNTHESIS = "synthesis"    # assembled from lemmas that were proved
    # An attempt at a HELPER, not at the goal. Added because its absence was
    # actively costing measurements: `math_v2` had nowhere to put a `try_lemma`
    # attempt and mapped it to DIRECT, so lemma work counted as tries at the
    # goal (inflating `mean_attempts`) and traces rendered "attempt 7: direct"
    # for something that never touched the goal.
    LEMMA = "lemma"            # an attempt at a helper result


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
class Telemetry:
    """What a proof attempt cost, in comparable units.

    Both provers populate this, so "the agentic one proves more" can be
    weighed against "it also spent four times the calls". A success rate
    without a budget is not a comparison.
    """

    model_calls: int = 0
    lean_calls: int = 0
    retrieval_calls: int = 0
    # Symbolic computation is the bridge between the two engines, and until
    # now it was tracked by the budget and reported nowhere. A run that never
    # computed and a run that computed twenty times looked identical.
    symbolic_calls: int = 0
    seconds: float = 0.0
    # WHAT THE RUN ACTUALLY COST. `model_calls` is a poor proxy for money: the
    # evaluation path keeps the whole message history, so each call carries
    # every prior turn and input grows with the run. MEASURED, on
    # eval/results/putnam-run2.json: 13 calls on the goal that bailed early
    # against 53 on the hardest one -- 4x the calls, but on the order of 16x
    # the input tokens once the growth is counted. A cost constraint that
    # cannot be read off the results file cannot be optimised against.
    #
    # Default 0 and reported as unknown when absent, so a provider whose
    # responses carry no usage metadata, and the baseline prover which does
    # not populate these at all, both stay honest rather than reading as free.
    input_tokens: int = 0
    output_tokens: int = 0
    # Did the counts above actually come back? They are read off the agent's
    # returned transcript, and a run that times out or crashes never returns
    # one -- so they stay at zero for a run that certainly called the model.
    # MEASURED: three of five goals in eval/results/putnam-run3.json reported
    # zero cost while having compiled Lean, and the run summary then totalled
    # the two survivors and presented it as the cost of five.
    #
    # Default True because a caller that says nothing is the ordinary,
    # complete case; only the harness's failure paths set it False.
    complete: bool = True
    # The compile CEILING this run was given, recorded next to what it spent.
    # Without it a results file cannot answer "how much budget was left when
    # the agent stopped", because the ceiling is an environment variable that
    # is not written down anywhere in the record -- and it moves between
    # profiles (12 by default, 40 under hard-reasoning).
    #
    # MEASURED, and the question it exists to settle: `putnam_1962_a4`
    # finished `not_proved` after ONE attempt at the goal with 32 of 40
    # compiles unused, twice. Whether that is good judgement (a1 and a3 stop
    # early too, and Mathlib genuinely lacks the machinery those need) or
    # premature timidity is not answerable from one goal, and guessing is how
    # a nudge gets added that makes a1 and a3 waste forty compiles each.
    lean_budget: int = 0

    def summary(self) -> str:
        tokens = ""
        if not self.complete:
            tokens = ", cost UNKNOWN (the run did not return a transcript)"
        elif self.input_tokens or self.output_tokens:
            tokens = (f", {self.input_tokens} in / "
                      f"{self.output_tokens} out tokens")
        return (
            f"{self.model_calls} model, {self.lean_calls} lean, "
            f"{self.retrieval_calls} retrieval, "
            f"{self.symbolic_calls} sympy, {self.seconds:.0f}s{tokens}"
        )


@dataclass
class ProofRun:
    """Explicit state for one proof attempt (Principle 5)."""

    goal: str                                   # the question, in English
    statement: str = ""                         # the goal, formalised
    statement_ok: bool = True                   # ...and Lean can elaborate it
    attempts: list[ProofAttempt] = field(default_factory=list)
    lemmas: list[Lemma] = field(default_factory=list)
    proof: str = ""                             # the accepted proof, if any
    # THE EVIDENCE FOR A `refuted`, kept for the same reason `proof` is kept
    # and previously not kept at all. A refutation is a COMPILER FACT of
    # exactly the same standing as a proof -- Lean accepted a complete proof
    # of the goal's negation -- and it is often the more publishable result,
    # because it says a benchmark statement is wrong.
    #
    # MEASURED: `exercise_3_22` and `exercise_5_15` were both refuted, and
    # neither refutation survived into the results file. `proof` was empty,
    # `stages` carried no refutation entry, and the only copies were in
    # `tempfile.mkdtemp` workspaces that Windows clears on its own schedule.
    # ProofNet's exercise_3_22 states Baire's theorem without `[Nonempty X]`;
    # that finding rested on a temp directory.
    #
    # Both fields, because a refutation is a proof OF A DIFFERENT STATEMENT --
    # the negation, which `try_refutation` builds -- so the goal's own
    # `statement` cannot be used to recompile it.
    refutation: str = ""                        # proof body of the negation
    refutation_statement: str = ""              # the negation it proves
    verdict: Verdict | None = None
    trace: list[str] = field(default_factory=list)
    review: object | None = None                # llm.reviewer.Review, if run
    telemetry: Telemetry = field(default_factory=Telemetry)

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

        if self.review is not None:
            lines.append(f"  {self.review.note()}")

        if self.proved:
            lines.append("  proof accepted by the compiler:")
            lines.extend(f"    {line}" for line in self.proof.splitlines())
        elif self.verdict is not None:
            lines.append(f"  {self.verdict.detail}")
        return "\n".join(lines)
