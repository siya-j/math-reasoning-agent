"""Autoformalisation: English mathematics in, Lean out.

This is the probabilistic half of theorem proving, and the honest weak point
of the whole system. Lean guarantees that a proof is correct. Nothing
guarantees that the STATEMENT says what the user asked — that translation
happens here, and it is exactly where failures 3 and 8 live.

Everything in this module is a model call. Nothing here decides anything.
The prover treats every string returned below as a proposal to be checked.

Design note: one class, injected wholesale into the prover. That is what
lets the entire proving pipeline be tested with no model and no API key —
tests pass a fake with the same five methods.
"""

from __future__ import annotations

import re

from llm.client import get_model
from retrieval.loogle import render_premises

_FENCE = re.compile(r"```(?:lean4?|)\n?(.*?)```", re.DOTALL)


def strip_fence(text: str) -> str:
    """Models wrap code in markdown fences. Lean does not read markdown."""
    match = _FENCE.search(text or "")
    return (match.group(1) if match else (text or "")).strip()


STATEMENT_PROMPT = """Translate this mathematical claim into a single Lean 4
theorem statement using Mathlib.

Claim: {goal}

Rules:
- Output ONLY the theorem signature, ending just before `:=`.
- Do not write a proof.
- State exactly the claim given. Do not strengthen, weaken or generalise it.
- Use Mathlib's standard names and notation.

Example shape: theorem my_claim (n : Nat) : n + 0 = n"""

SKETCH_PROMPT = """Prove this claim in ordinary mathematical English.

Claim: {goal}

Be concise and name the key theorems or lemmas you rely on. This sketch is
guidance for writing a formal proof; it will not itself be accepted as one."""

PROOF_PROMPT = """Write a Lean 4 proof of this theorem, using Mathlib.

Theorem: {statement}

Informal proof for guidance:
{sketch}
{premises}{errors}
Rules:
- Output ONLY the proof body — what follows `:=`.
- Never use `sorry` or `admit`. An incomplete proof is worse than none.
- Do not introduce new axioms.
- Do not restate the theorem."""

ERRORS_BLOCK = """
Your previous attempt was:
{previous}

The compiler rejected it:
{errors}

Repair that attempt. Fix the specific errors above rather than starting over.
"""

LEMMAS_PROMPT = """This claim could not be proved directly:

{goal}

Propose {count} auxiliary lemmas that would help find a strategy. They may be
special cases, concrete instances, or facts that follow from the assumptions —
they do NOT have to be steps in the final proof.

Every lemma must be true if the original claim is true.

Output one lemma per line, in plain English, with no numbering or commentary."""

SYNTHESIS_PROMPT = """Prove this theorem in Lean 4, using Mathlib.

Theorem: {statement}

The following lemmas have already been proved and may be used. Their proofs
compile, so you may rely on them:

{lemmas}

Rules:
- Output ONLY the proof body — what follows `:=`.
- Include the lemma statements and proofs above the main theorem if you use
  them, so the file compiles on its own.
- Never use `sorry` or `admit`."""


class Formalizer:
    """The five model calls theorem proving needs.

    `search` is optional. Without it the model writes Mathlib names from
    memory, against a library of roughly 167,000 declarations — which is
    guessing. With it, real names are put in front of the model first.
    """

    def __init__(self, model=None, search=None):
        self._model = model or get_model()
        self._search = search
        # Premises depend only on the statement, which does not change across
        # the five attempts on a goal. Without this, a single goal made up to
        # forty identical HTTP requests.
        self._premise_cache: dict[str, str] = {}

    def _ask(self, prompt: str) -> str:
        result = self._model.invoke(prompt)
        return getattr(result, "text", None) or getattr(result, "content", "") or ""

    def statement(self, goal: str) -> str:
        """English claim -> Lean theorem signature."""
        return strip_fence(self._ask(STATEMENT_PROMPT.format(goal=goal)))

    def sketch(self, goal: str) -> str:
        """An informal proof, used only as context for the formal one."""
        return self._ask(SKETCH_PROMPT.format(goal=goal)).strip()

    def _premises(self, statement: str) -> str:
        """Retrieved Mathlib declarations, or nothing if search is unavailable.

        Retrieval failing is not an error. It degrades to the behaviour this
        class had before search existed.
        """
        if self._search is None:
            return ""
        if statement not in self._premise_cache:
            self._premise_cache[statement] = render_premises(
                self._search.premises_for(statement)
            )
        return self._premise_cache[statement]

    def proof(
        self, statement: str, sketch: str, errors: str = "", previous: str = ""
    ) -> str:
        """Lean proof body.

        Supplying `errors` and `previous` turns this into a refinement step:
        Prover Agent (§3.1) feeds back the previous attempt *together with*
        the error messages, so the model repairs a draft rather than starting
        again from nothing.
        """
        block = (
            ERRORS_BLOCK.format(errors=errors, previous=previous or "(not recorded)")
            if errors
            else ""
        )
        return strip_fence(
            self._ask(
                PROOF_PROMPT.format(
                    statement=statement,
                    sketch=sketch,
                    premises=self._premises(statement),
                    errors=block,
                )
            )
        )

    def lemmas(self, goal: str, count: int) -> list[str]:
        """Auxiliary facts, in English, for bottom-up strategy discovery."""
        raw = self._ask(LEMMAS_PROMPT.format(goal=goal, count=count))
        lines = [line.strip(" -*\t") for line in raw.splitlines()]
        return [line for line in lines if line][:count]

    def synthesis(self, statement: str, lemmas: list[str]) -> str:
        """Assemble a final proof from lemmas that already compiled."""
        listed = "\n".join(f"- {text}" for text in lemmas)
        return strip_fence(
            self._ask(SYNTHESIS_PROMPT.format(statement=statement, lemmas=listed))
        )
