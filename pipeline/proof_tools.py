"""Tools for the agentic prover, and the log that keeps it honest.

THE SAME INVERSION AS THE VERIFICATION PATH
-------------------------------------------
`pipeline/tools.py` records a Check as a side effect of a tool being CALLED,
so the guard reads execution rather than the model's report of execution.
This does the same for proofs: every compilation the agent requests is
recorded here, and the verdict is computed from those records.

The agent may claim anything in prose. If no recorded attempt compiled, the
result is NOT PROVED — exactly as an agent that calls no verification tool
gets NOT VERIFIED.

WHY TOOLS RATHER THAN A PIPELINE
--------------------------------
The baseline prover asks for a whole proof five times through a stateless
function. Each call is a fresh mind: it cannot ask for another lemma, cannot
see what a tactic did, cannot remember that attempt 2 failed for the reason
attempt 4 is about to repeat. Five attempts are one attempt, five times.

Here the model decides when to search, when to compile, and what to do with
what it learns — and the conversation is the memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import config
from domain.proof import ProofAttempt, ProofStage, Telemetry
from domain.verdict import Verdict, VerificationStatus
from pipeline.tactics import cheap_attempt
from retrieval.loogle import Premise, render_premises


class BudgetExhausted(RuntimeError):
    """Raised to unwind an agent that will not stop when asked."""


@dataclass
class Budget:
    """A hard stop on an agent that can otherwise loop forever.

    Giving a model the wheel means it can also drive in circles. Observed:
    a near-mathlib goal that never terminated and had to be interrupted by
    hand — no proof, no verdict, no record.

    Enforced in TWO stages, because a polite request is not a guarantee:

      1. At the limit, every tool returns "STOP" instead of doing work. The
         agent gets a chance to conclude cleanly and report what it has.
      2. After a short grace, tools raise. `agentic_prover` catches that,
         keeps everything recorded so far, and reports honestly.

    Stage 2 is what makes termination a property of the code rather than a
    hope about the model.
    """

    max_tool_calls: int = 20
    max_lean_calls: int = 8
    max_searches: int = 8
    max_consecutive_searches: int = 3
    max_seconds: float = 300.0
    started: float = field(default_factory=time.monotonic)

    tool_calls: int = 0
    lean_calls: int = 0
    searches: int = 0
    searches_since_compile: int = 0
    grace: int = 3          # tool calls allowed after the limit, then raise
    reason: str = ""

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining(self) -> float:
        return self.max_seconds - self.elapsed

    def _over(self, lean: bool) -> tuple[str, str]:
        """Which limit blocks THIS call: (kind, message). ("", "") to proceed.

        Each budget bounds only what it names. A spent compilation budget
        must not block a search — searches cost milliseconds where a compile
        costs twenty seconds — and termination is still guaranteed, because
        `max_tool_calls` bounds everything.
        """
        if self.elapsed > self.max_seconds:
            return "time", f"time budget spent ({self.max_seconds:.0f}s)"
        if self.tool_calls >= self.max_tool_calls:
            return "tool", f"tool budget spent ({self.max_tool_calls} calls)"
        if lean:
            if self.lean_calls >= self.max_lean_calls:
                return "lean", f"compilation budget spent ({self.max_lean_calls} compiles)"
            # Refusing to START a compile that cannot finish inside the budget.
            # Measured overshoot without this: 494s against a 300s budget,
            # because a compile begun at 290s still runs a full LEAN_TIMEOUT.
            if self.remaining < config.LEAN_TIMEOUT:
                return "time", (
                    f"time budget spent ({self.max_seconds:.0f}s) — too little "
                    "left to finish a compilation"
                )
        return "", ""

    def spend(self, *, lean: bool = False, search: bool = False) -> str:
        """Charge one tool call. Returns a STOP/redirect message, or "".

        Two kinds of message come back, and they mean different things:

        STOP     the run is over; nothing useful is left to do.
        ENOUGH   this particular tool is spent, but the run is not. The agent
                 is pushed towards the work it has been avoiding.
        """
        kind, over = self._over(lean)
        if over:
            self.reason = over
            # Grace exists so the agent can conclude cleanly rather than be
            # cut off mid-thought. That reasoning does not apply to the clock:
            # each graced round trip is spent in the very currency that ran
            # out, which is how a 300s budget became 494s.
            if kind == "time":
                self.grace = min(self.grace, 1)
            self.grace -= 1
            if self.grace < 0:
                raise BudgetExhausted(over)
            return (
                f"STOP: {over}. Do not call any more tools. If nothing has been "
                "accepted, say so plainly and finish."
            )

        self.tool_calls += 1
        if lean:
            self.lean_calls += 1
            self.searches_since_compile = 0
        if search:
            self.searches += 1
            self.searches_since_compile += 1

        # A redirect is charged like any other call, so an agent that only
        # ever searches is still bounded by max_tool_calls.
        if search:
            left = self.max_lean_calls - self.lean_calls
            if self.searches > self.max_searches:
                return (
                    f"ENOUGH SEARCHING: {self.max_searches} searches used and "
                    f"nothing compiled yet. You have {left} compilation(s) left. "
                    "Write a proof from what you already have."
                )
            if self.searches_since_compile > self.max_consecutive_searches:
                return (
                    f"ENOUGH SEARCHING: {self.searches_since_compile - 1} searches "
                    "in a row without compiling. Compile something. A rejected "
                    "attempt returns the goal state, which will tell you more "
                    f"than another query. You have {left} compilation(s) left."
                )
        return ""


@dataclass
class ProofLog:
    """Everything the agent actually did. The verdict comes from here."""

    statement: str
    attempts: list[ProofAttempt] = field(default_factory=list)
    premises: list[Premise] = field(default_factory=list)
    telemetry: Telemetry = field(default_factory=Telemetry)
    trace: list[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)

    @property
    def accepted(self) -> ProofAttempt | None:
        """The first attempt the compiler accepted, if any."""
        for attempt in self.attempts:
            if attempt.verdict.status is VerificationStatus.TRUE:
                return attempt
        return None

    def record(self, stage: ProofStage, proof: str, verdict: Verdict) -> None:
        self.attempts.append(
            ProofAttempt(len(self.attempts) + 1, stage, proof, verdict)
        )
        self.trace.append(f"{stage.value}: {verdict.status.value}")


def make_proof_tools(log: ProofLog, check, search=None) -> list:
    """Tools for one proof attempt. `check` and `search` are injected.

    Docstrings are the tool descriptions the model sees, so they carry no
    concrete Lean examples — a model once copied a docstring's example
    verbatim into an unrelated goal.
    """

    def search_mathlib(query: str) -> str:
        """Search Mathlib for declarations matching a query.

        Use this whenever you need a lemma and are unsure of its exact name
        or signature. Search as often as you like; guessing a name that does
        not exist wastes a compile.

        query: either a declaration name fragment, or a type pattern.
            Prefix with `|- ` to find declarations whose CONCLUSION matches,
            which is usually what closes a goal. Separate a hypothesis shape
            and a conclusion with a comma to narrow further.
        """
        stop = log.budget.spend(search=True)
        if stop:
            return stop

        log.telemetry.retrieval_calls += 1
        if search is None:
            return "Search is unavailable. Rely on names you are certain of."

        found = search.search(query, limit=config.PREMISES_PER_QUERY)
        for premise in found:
            if premise.name not in {p.name for p in log.premises}:
                log.premises.append(premise)

        # Record the NAMES, not just the count. A count cannot distinguish
        # "retrieval never surfaced the lemma" from "retrieval surfaced it and
        # the agent ignored it", and those call for opposite fixes.
        names = ", ".join(p.name for p in found[:8]) if found else "nothing"
        log.trace.append(f"search: {query!r} -> {names}")
        if not found:
            return f"No declarations match {query!r}. Try a broader pattern."
        return render_premises(found)

    def try_proof(proof: str) -> str:
        """Compile a candidate proof and report exactly what Lean said.

        This is the only thing that can establish a proof. Use it as often as
        needed — a rejected attempt costs nothing but time, and the goal state
        it returns is the most useful information available.

        proof: the proof body, what follows `:=`. Do not restate the theorem.
            Never use `sorry` or `admit`; they compile and prove nothing.
        """
        stop = log.budget.spend(lean=True)
        if stop:
            return stop

        log.telemetry.lean_calls += 1
        verdict = check(log.statement, proof)
        log.record(ProofStage.DIRECT, proof, verdict)

        if verdict.status is VerificationStatus.TRUE:
            return "ACCEPTED. The proof compiles. You are done; stop here."
        return f"REJECTED.\n{verdict.detail}"

    def try_standard_tactics() -> str:
        """Try the usual closers and every retrieved premise in one compile.

        Runs `simp`, `norm_num`, `decide`, `aesop` and similar, plus `exact`
        and `apply` forms against everything found so far. Cheap and worth
        trying before writing a proof by hand.
        """
        stop = log.budget.spend(lean=True)
        if stop:
            return stop

        log.telemetry.lean_calls += 1
        candidate = cheap_attempt(log.premises)
        verdict = check(log.statement, candidate)
        log.record(ProofStage.CHEAP, candidate, verdict)

        if verdict.status is VerificationStatus.TRUE:
            return "ACCEPTED. One of the standard tactics closed the goal."
        return (
            "None of the standard tactics closed it. You will need to write "
            f"the proof.\n{verdict.detail[:600]}"
        )

    return [search_mathlib, try_proof, try_standard_tactics]
