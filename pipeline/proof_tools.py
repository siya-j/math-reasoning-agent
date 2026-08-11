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

from dataclasses import dataclass, field

import config
from domain.proof import ProofAttempt, ProofStage, Telemetry
from domain.verdict import Verdict, VerificationStatus
from pipeline.tactics import cheap_attempt
from retrieval.loogle import Premise, render_premises


@dataclass
class ProofLog:
    """Everything the agent actually did. The verdict comes from here."""

    statement: str
    attempts: list[ProofAttempt] = field(default_factory=list)
    premises: list[Premise] = field(default_factory=list)
    telemetry: Telemetry = field(default_factory=Telemetry)
    trace: list[str] = field(default_factory=list)

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
        log.telemetry.retrieval_calls += 1
        if search is None:
            return "Search is unavailable. Rely on names you are certain of."

        found = search.search(query, limit=config.PREMISES_PER_QUERY)
        for premise in found:
            if premise.name not in {p.name for p in log.premises}:
                log.premises.append(premise)

        log.trace.append(f"search: {query!r} -> {len(found)} result(s)")
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
