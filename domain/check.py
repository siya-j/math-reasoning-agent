"""One verification the agent performed.

The agent decides WHICH checks to run. It does not decide what they return.

`claim` records what the agent believed it was testing. It exists because of
a real failure: asked "is 2 the only solution of x^2 = 4?", the agent checked
"are the solutions 2 and -2?" instead. Every component behaved correctly and
the answer was still wrong, because nothing recorded WHICH claim the check
was for. Recording it does not prevent the substitution, but it makes it
visible instead of silent.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.verdict import Verdict
from domain.verification import VerificationRequest


@dataclass(frozen=True)
class Check:
    """A tool call the agent made, and the verifier's answer."""

    tool: str
    claim: str
    request: VerificationRequest
    verdict: Verdict

    def summary(self) -> str:
        return f"[{self.verdict.status.value}] {self.claim or self.tool}"

    def detail_line(self) -> str:
        arguments = self.request.lhs
        if self.request.rhs:
            arguments += f" ?= {self.request.rhs}"
        if self.request.candidate:
            arguments += f"  candidate: {self.request.candidate}"
        return f"{self.tool}({arguments})"
