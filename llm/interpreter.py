"""Claim interpretation and problem classification (Execution Flow steps 1-2).

Restored. An earlier version of this module existed, was orphaned by the
agent rewrite, and sat unimportable in the repository — which is why the
pipeline had five stages where the design document specifies seven.

Turns messy natural language into a `domain.Claim`: what is being asserted,
and which kind of system could settle it.

THIS DECIDES NOTHING ABOUT TRUTH. It picks a route. A misclassification
costs a wasted attempt; the guard and the compiler still have the last word,
and `pipeline.router` falls back to the other engine when the first finds
nothing.
"""

from __future__ import annotations

import re

from domain.claim import Claim, ProblemType
from llm.client import get_model

INTERPRET_PROMPT = """Classify a mathematical question, so it can be sent to
the right system.

Question: {question}

The systems available are:

  computational  a computer algebra system (SymPy). Use for anything that can
                 be CALCULATED on concrete expressions: arithmetic,
                 derivatives, integrals, limits, primality, factorisation,
                 solving equations, matrices, series, inequalities in one
                 variable, algebraic identities.

  formal         a proof assistant (Lean with Mathlib). Use for general
                 mathematical statements that must be PROVED rather than
                 computed: claims about all groups, all vector spaces,
                 topology, analysis, set theory, cardinality, or any
                 statement quantified over an infinite structure.

  unsupported    neither applies: opinion, history, or not mathematics.

Answer in exactly this format, three lines, nothing else:

TYPE: computational
CLAIM: a one-sentence restatement of exactly what is being asserted
WHY: a short reason for the classification

Restate the claim as the user made it. Do not correct it, strengthen it or
weaken it — if the user's claim is false, the restatement must be the false
claim, because that is what will be checked."""

_FIELD = r"^\s*{}\s*:\s*(.+)$"


def _field(text: str, name: str) -> str:
    match = re.search(_FIELD.format(name), text or "", re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def parse_classification(text: str, question: str) -> Claim:
    """Read the model's three lines into a Claim.

    Unparseable output falls back to COMPUTATIONAL: it is the cheaper engine,
    it is deterministic, and the router will try the prover afterwards anyway.
    Defaulting to `formal` would spend a proof budget on `2 + 2`.
    """
    raw_type = _field(text, "TYPE").lower()
    for candidate in ProblemType:
        if candidate.value in raw_type:
            problem_type = candidate
            break
    else:
        problem_type = ProblemType.COMPUTATIONAL

    return Claim(
        question=question,
        statement=_field(text, "CLAIM"),
        problem_type=problem_type,
        reason=_field(text, "WHY"),
    )


class Interpreter:
    """One model call: question in, Claim out."""

    def __init__(self, model=None):
        self._model = model or get_model()

    def interpret(self, question: str) -> Claim:
        try:
            reply = self._model.invoke(INTERPRET_PROMPT.format(question=question))
            text = getattr(reply, "text", None) or getattr(reply, "content", "") or ""
        except Exception as exc:
            # Interpretation is a routing convenience. If the model is
            # unreachable, route computationally rather than failing the run.
            return Claim(
                question=question,
                problem_type=ProblemType.COMPUTATIONAL,
                reason=f"classification unavailable ({exc})",
            )
        return parse_classification(text, question)
