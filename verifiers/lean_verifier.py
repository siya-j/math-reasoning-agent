"""Lean proof checking (Design Doc Phase 6 — formal verification).

SymPy decides computations. This decides *proofs*, which is what abstract
mathematics needs: topology, group theory, analysis, set theory — the twelve
claims in the golden set that a computer algebra system can only refuse.

THE RULE THAT MAKES THIS SOUND
------------------------------
A failed Lean proof means UNKNOWN. It never means FALSE.

Lean failing to compile a proof of P is not evidence against P. It nearly
always means the proof was wrong, or Mathlib spells the lemma differently,
or the statement was mis-transcribed. Reading failure as refutation would
make this verifier declare true theorems false — a worse failure than
having no verifier at all.

So this class returns TRUE or UNKNOWN, and nothing else. Refuting a claim
means proving its negation, which is a different request.

WHAT LEAN ACTUALLY DOES
-----------------------
Lean CHECKS proofs; it does not FIND them. Something else must supply the
proof. For theorems already in Mathlib that is close to a citation. For
novel claims it is an open research problem, and this verifier will
honestly return UNKNOWN.
"""

from __future__ import annotations

import re

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers.base import Verifier
from verifiers.lean_runner import LeanOutcome, LeanResult, run_lean

# Mathlib is where the formalised mathematics lives. Without it, almost no
# interesting statement can even be expressed.
DEFAULT_PREAMBLE = "import Mathlib\n"


GOAL_NAME = "mra_goal"

# `theorem <name>` / `lemma <name>` starting a line. MULTILINE because proved
# auxiliary lemmas are prepended to the goal, so the file may hold several
# declarations; a lemma body is indented and cannot match.
_DECLARED_NAME = re.compile(
    r"^([ \t]*(?:theorem|lemma)[ \t]+)([A-Za-z_][\w'.]*)", re.MULTILINE
)


def rename_goal(statement: str, name: str = GOAL_NAME) -> str:
    """Give the goal a name that cannot collide with the library.

    A formalizer asked for "the square root of 2 is irrational" produces:

        theorem irrational_sqrt_two : Irrational (Real.sqrt 2)

    which is the name Mathlib already uses. The proof `exact
    irrational_sqrt_two` is then correct AND impossible:

        error: `irrational_sqrt_two` has already been declared

    The collision is at DECLARATION, so no proof can work around it — the
    agent tried `_root_.` and was defeated by a problem upstream of it. And
    the better the formalizer names things, the more often this fires,
    because a good name for a known theorem IS the library's name. It hits
    precisely the in-mathlib and near-mathlib tiers.

    Renaming here rather than at formalisation keeps every path covered:
    both provers, the tactic ladder and the skeleton all build files through
    this function.

    THE LAST DECLARATION, NOT THE FIRST. When auxiliary lemmas the agent has
    proved are prepended to the goal, the goal is last. The lemmas must keep
    their names — the goal's proof cites them — and only the goal may be
    renamed. With no lemmas the last declaration is the only one, so this is
    unchanged for every other caller.
    """
    matches = list(_DECLARED_NAME.finditer(statement))
    if not matches:
        return statement
    last = matches[-1]
    return statement[: last.start()] + last.group(1) + name + statement[last.end():]


def declaration(statement: str, proof: str) -> str:
    """One complete Lean declaration: a signature with its proof attached."""
    statement = statement.strip()
    proof = proof.strip()

    # A bare tactic ("exact foo") needs a `by` block; a term proof or an
    # explicit `by ...` is already well formed and must be left alone.
    if not proof.startswith(("by", ":=")):
        proof = f"by\n  {proof}"

    separator = "" if statement.endswith(":=") else " :="
    return f"{statement}{separator} {proof}"


def build_source(statement: str, proof: str, preamble: str = DEFAULT_PREAMBLE) -> str:
    """Assemble a complete Lean file from a theorem and its proof."""
    return f"{preamble}\n{declaration(rename_goal(statement.strip()), proof)}\n"


class LeanVerifier(Verifier):
    """Accepts FORMAL requests and asks Lean whether the proof stands."""

    name = "lean"

    def __init__(self, runner=run_lean, preamble: str = DEFAULT_PREAMBLE):
        # The runner is injected so the policy below can be tested without a
        # Lean installation. Every rule here is a rule about MEANING, and
        # meaning should not require a 3 GB toolchain to test.
        self._run = runner
        self._preamble = preamble

    def supports(self, request: VerificationRequest) -> bool:
        return request.kind is VerificationKind.FORMAL

    def verify(self, request: VerificationRequest) -> Verdict:
        if not request.statement.strip():
            return self._unknown("No theorem statement was supplied.")
        if not request.proof.strip():
            return self._unknown(
                "No proof was supplied. Lean checks proofs; it does not "
                "search for them."
            )

        source = build_source(request.statement, request.proof, self._preamble)

        try:
            result = self._run(source)
        except Exception as exc:  # a verifier must never take the pipeline down
            return self._unknown(f"Lean could not be run: {exc}")

        return self._interpret(result, request.statement)

    # --------------------------------------------------------------- policy
    def _interpret(self, result: LeanResult, statement: str) -> Verdict:
        return interpret(result, statement, method=self.name)

    def _unknown(self, detail: str) -> Verdict:
        return unknown(detail, method=self.name)


def unknown(detail: str, method: str = "lean") -> Verdict:
    return Verdict(status=VerificationStatus.UNKNOWN, method=method, detail=detail)


def interpret(result: LeanResult, statement: str, method: str = "lean") -> Verdict:
    """What a compiler result MEANS. Pure policy, no I/O.

    Public because the math_v2 tools dispatch Lean through a CommandSpec and
    then need exactly this decision, without the synchronous runner that
    `LeanVerifier.verify` owns. One implementation, two callers.
    """
    if result.outcome is LeanOutcome.COMPILED:
        return Verdict(
            status=VerificationStatus.TRUE,
            method=method,
            detail=f"Lean accepted a complete proof of: {statement}",
        )

    if result.outcome is LeanOutcome.INCOMPLETE:
        return unknown(
            "The proof compiles but uses `sorry` or `admit`, which proves "
            "nothing. Lean accepted a placeholder, not an argument.",
            method,
        )

    if result.outcome is LeanOutcome.CHEATED:
        return unknown(
            "The proof compiles, but only by using "
            f"{result.output}. Compiling is not the same as proving.",
            method,
        )

    if result.outcome is LeanOutcome.ERRORS:
        # Whole error blocks, not just header lines: Lean puts the goal state
        # on the lines that follow, and that is the single most useful thing a
        # refinement attempt can be told.
        listed = "\n".join(result.errors[:5]) or result.first_error

        # Repeat the goals at the end. Buried in a wall of context they are
        # easy to miss; stated plainly they are the instruction.
        goals = result.goals
        if goals:
            remaining = "\n".join(goals[:3])
            listed = f"{listed}\n\nStill to prove:\n{remaining}"

        return unknown(
            "Lean rejected the proof, which does NOT make the claim false "
            f"\u2014 only unproved.\n{listed}",
            method,
        )

    if result.outcome is LeanOutcome.TIMEOUT:
        return unknown(
            "Lean did not finish within the time budget. Slow is not false.",
            method,
        )

    return unknown(
        "Lean is not installed on this machine, so the claim could not be "
        "checked. Install Lean and Mathlib, or set MRA_LEAN to the binary.",
        method,
    )
