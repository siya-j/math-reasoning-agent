"""One entry point (Design Doc §10 — the complete execution flow).

    User Input
      -> Claim Interpretation      llm/interpreter.py
      -> Problem Classification    computational | formal | unsupported
      -> Reasoning + Verification  SymPy  or  Lean
      -> Explanation
      -> Final Response

Before this module the project had two disconnected programs: a verifier
that could never reach Lean, and a prover nobody routed to. This joins them.

FALLBACK, AND WHY IT MATTERS
----------------------------
Classification is a model's guess. A wrong guess must cost an attempt, never
an answer — so whichever engine runs first, if it settles nothing, the other
one is tried. A claim is only reported unsettled after both have declined.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.claim import Claim, ProblemType
from domain.proof import ProofRun
from domain.state import AgentRun
from llm.interpreter import Interpreter
from pipeline import pipeline as verification
from pipeline import prover as proving


@dataclass
class Answer:
    """What the agent did, and what it concluded."""

    claim: Claim
    verification: AgentRun | None = None
    proof: ProofRun | None = None
    trace: list[str] = field(default_factory=list)

    @property
    def settled(self) -> bool:
        """Did anything deterministic actually decide this?"""
        if self.proof is not None and self.proof.proved:
            return True
        return self.verification is not None and self.verification.verdict.was_verified

    def report(self) -> str:
        lines = [
            f"question: {self.claim.question}",
            f"routed as: {self.claim.problem_type.value}"
            + (f" — {self.claim.reason}" if self.claim.reason else ""),
            "",
        ]
        if self.proof is not None:
            lines.append(self.proof.report())
            lines.append("")
        if self.verification is not None:
            lines.append(self.verification.answer)
        if not self.settled and self.proof is None and self.verification is None:
            lines.append("[NOT VERIFIED] Nothing here could be checked.")
        return "\n".join(lines)


def ask(
    question: str,
    interpreter: Interpreter | None = None,
    verify=verification.run,
    prove=proving.prove,
) -> Answer:
    """Interpret, classify, route, and fall back. Engines are injected."""
    interpreter = interpreter or Interpreter()
    claim = interpreter.interpret(question)
    answer = Answer(claim=claim)
    answer.trace.append(f"classified: {claim.problem_type.value}")

    if claim.problem_type is ProblemType.FORMAL:
        _prove(answer, prove)
        if not answer.settled:
            answer.trace.append("fallback: trying computational verification")
            _verify(answer, verify)
        return answer

    # COMPUTATIONAL and UNSUPPORTED both start with the cheap deterministic
    # engine. UNSUPPORTED is not refused outright — the guard reports NOT
    # VERIFIED honestly, and that is a better answer than a refusal to look.
    _verify(answer, verify)
    if not answer.settled and claim.problem_type is not ProblemType.UNSUPPORTED:
        answer.trace.append("fallback: trying formal proof")
        _prove(answer, prove)
    return answer


def _verify(answer: Answer, verify) -> None:
    try:
        answer.verification = verify(answer.claim.text)
    except Exception as exc:
        answer.trace.append(f"verification failed: {exc}")


def _prove(answer: Answer, prove) -> None:
    try:
        answer.proof = prove(answer.claim.text)
    except Exception as exc:
        answer.trace.append(f"proving failed: {exc}")
