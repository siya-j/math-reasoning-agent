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

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers.base import Verifier
from verifiers.lean_runner import LeanOutcome, LeanResult, run_lean

# Mathlib is where the formalised mathematics lives. Without it, almost no
# interesting statement can even be expressed.
DEFAULT_PREAMBLE = "import Mathlib\n"


def build_source(statement: str, proof: str, preamble: str = DEFAULT_PREAMBLE) -> str:
    """Assemble a complete Lean file from a theorem and its proof."""
    statement = statement.strip()
    proof = proof.strip()

    # A bare tactic ("exact foo") needs a `by` block; a term proof or an
    # explicit `by ...` is already well formed and must be left alone.
    if not proof.startswith(("by", ":=")):
        proof = f"by\n  {proof}"

    separator = "" if statement.endswith(":=") else " :="
    return f"{preamble}\n{statement}{separator} {proof}\n"


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
        if result.outcome is LeanOutcome.COMPILED:
            return Verdict(
                status=VerificationStatus.TRUE,
                method=self.name,
                detail=f"Lean accepted a complete proof of: {statement}",
            )

        if result.outcome is LeanOutcome.INCOMPLETE:
            return self._unknown(
                "The proof compiles but uses `sorry` or `admit`, which proves "
                "nothing. Lean accepted a placeholder, not an argument."
            )

        if result.outcome is LeanOutcome.CHEATED:
            return self._unknown(
                "The proof compiles, but only by using "
                f"{result.output}. Compiling is not the same as proving."
            )

        if result.outcome is LeanOutcome.ERRORS:
            # Every error, not just the first: refinement is only as good as
            # the feedback it receives.
            listed = "\n".join(result.errors[:5]) or result.first_error
            return self._unknown(
                "Lean rejected the proof, which does NOT make the claim false "
                f"— only unproved.\n{listed}"
            )

        if result.outcome is LeanOutcome.TIMEOUT:
            return self._unknown(
                "Lean did not finish within the time budget. Slow is not false."
            )

        return self._unknown(
            "Lean is not installed on this machine, so the claim could not be "
            "checked. Install Lean and Mathlib, or set MRA_LEAN to the binary."
        )

    def _unknown(self, detail: str) -> Verdict:
        return Verdict(
            status=VerificationStatus.UNKNOWN, method=self.name, detail=detail
        )
