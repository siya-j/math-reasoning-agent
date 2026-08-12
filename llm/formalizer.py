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
from llm.retry import call_with_backoff
from retrieval.loogle import render_premises

_FENCE = re.compile(r"```(?:lean4?|)\n?(.*?)```", re.DOTALL)


def strip_fence(text: str) -> str:
    """Models wrap code in markdown fences. Lean does not read markdown."""
    match = _FENCE.search(text or "")
    return (match.group(1) if match else (text or "")).strip()


LEAN_CONTEXT = """You are producing Lean 4 for Mathlib. Conventions that apply
to everything below:

- Mathlib is imported. Use its real names; do not invent identifiers.
- Citing an existing lemma beats constructing an argument. If a retrieved
  premise closes the goal, use it.
- `sorry` and `admit` are worse than failing. They compile and prove nothing.
- Never introduce an `axiom`, and never leave `exact?`, `apply?` or `simp?`
  in an answer — those report candidates rather than committing to a proof.
- Mathlib's argument order is often not the obvious one. Prefer the exact
  signature of a retrieved premise over what you remember.

"""

STATEMENT_PROMPT = LEAN_CONTEXT + """Translate this mathematical claim into a
single Lean 4 theorem statement using Mathlib.

Claim: {goal}

Rules:
- Output ONLY the theorem signature, ending just before `:=`.
- Do not write a proof.
- State exactly the claim given. Do not strengthen, weaken or generalise it.
- Use Mathlib's standard names and notation.

Example shape: theorem my_claim (n : Nat) : n + 0 = n"""

REPAIR_PROMPT = LEAN_CONTEXT + """This Lean 4 theorem statement does not
compile. The problem is in the STATEMENT itself, not in any proof.

Claim: {goal}

Statement:
{statement}

Lean reported:
{errors}
{hints}{history}
Rules:
- Output ONLY the corrected theorem signature, ending just before `:=`.
- Do not write a proof.
- Fix NAMES and NOTATION. Mathlib renames things, and a name you remember may
  now live in a different namespace.
- Do NOT change what the statement says. Do not weaken it, strengthen it, add
  a hypothesis that makes it easier, or replace it with a related claim that
  happens to compile. A statement that compiles but says something else is
  worse than one that does not compile at all."""

SKETCH_PROMPT = """Prove this claim in ordinary mathematical English.

Claim: {goal}

Be concise and name the key theorems or lemmas you rely on. This sketch is
guidance for writing a formal proof; it will not itself be accepted as one."""

PROOF_PROMPT = LEAN_CONTEXT + """Write a Lean 4 proof of this theorem, using Mathlib.

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

SKELETON_PROMPT = LEAN_CONTEXT + """Break this theorem into steps. Do NOT prove it.

Theorem: {statement}

Informal proof for guidance:
{sketch}
{premises}
Write a Lean 4 proof SKELETON: a sequence of `have` steps whose claims lead to
the goal, each one deferred.

Rules:
- Output ONLY the proof body — what follows `:=`.
- Every step must be exactly `have <name> : <claim> := by sorry`.
- The final line must close the goal FROM those steps, with no `sorry`.
- Aim for {count} steps or fewer. Each should be a claim a competent Lean user
  could discharge in a line or two.
- The claims matter, not the proofs. State them precisely and in Lean syntax.

Example shape:
  have h1 : 0 < n := by sorry
  have h2 : n ≠ 0 := by sorry
  exact foo h1 h2"""

HOLE_PROMPT = LEAN_CONTEXT + """Prove one small Lean 4 goal, using Mathlib.

Goal: {claim}

It arises inside this proof, where the other steps may be assumed:
{context}
{premises}
Rules:
- Output ONLY the tactic block that proves this goal — what follows `by`.
- One or two tactics if possible.
- Never use `sorry` or `admit`."""

LEMMAS_PROMPT = """This claim could not be proved directly:

{goal}

Propose {count} auxiliary lemmas that would help find a strategy. They may be
special cases, concrete instances, or facts that follow from the assumptions —
they do NOT have to be steps in the final proof.

Every lemma must be true if the original claim is true.

Output one lemma per line, in plain English, with no numbering or commentary."""

SYNTHESIS_PROMPT = LEAN_CONTEXT + """Prove this theorem in Lean 4, using Mathlib.

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
        self._premise_objects: dict[str, list] = {}

    def _ask(self, prompt: str) -> str:
        # Backoff on limits that waiting may clear. Without it a single
        # transient error ends a proof run that the verification path,
        # which has had retries since Phase 4, would have survived.
        result = call_with_backoff(lambda: self._model.invoke(prompt))
        return getattr(result, "text", None) or getattr(result, "content", "") or ""

    def statement(self, goal: str) -> str:
        """English claim -> Lean theorem signature."""
        return strip_fence(self._ask(STATEMENT_PROMPT.format(goal=goal)))

    def repair_statement(
        self, goal: str, statement: str, errors: str, hints: str = "",
        history: tuple = (),
    ) -> str:
        """A statement Lean rejected -> one corrected attempt.

        The only feedback loop formalisation has. Everything else in this
        class is asked once and never told whether it worked.

        `history` is every earlier rejected version with its error. Without it
        a second call is a fresh mind given the same prompt, which produces
        the same answer — the failure that motivated the agentic prover.
        """
        earlier = ""
        if len(history) > 1:
            earlier = "\n\nVersions already tried and rejected — do not repeat these:\n"
            for index, (attempt, error) in enumerate(history, start=1):
                earlier += f"\n{index}. {attempt}\n   Lean: {error[:200]}\n"

        return strip_fence(
            self._ask(
                REPAIR_PROMPT.format(
                    goal=goal, statement=statement, errors=errors[:1500],
                    hints=hints, history=earlier,
                )
            )
        )

    def sketch(self, goal: str) -> str:
        """An informal proof, used only as context for the formal one."""
        return self._ask(SKETCH_PROMPT.format(goal=goal)).strip()

    def premises_for(self, statement: str) -> list:
        """Retrieved premises as objects, for the deterministic tactic attempt.

        Cached alongside the rendered form so the mechanical attempt and the
        prompt share one set of lookups.
        """
        if self._search is None:
            return []
        if statement not in self._premise_objects:
            self._premise_objects[statement] = self._search.premises_for(statement)
        return self._premise_objects[statement]

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

    def skeleton(self, statement: str, sketch: str, count: int = 4) -> str:
        """A proof decomposed into deferred `have` steps.

        The claims are what matter. A skeleton that compiles WITH `sorry`
        proves the decomposition typechecks, which turns one hard problem
        into several independent easy ones.
        """
        return strip_fence(
            self._ask(
                SKELETON_PROMPT.format(
                    statement=statement,
                    sketch=sketch,
                    premises=self._premises(statement),
                    count=count,
                )
            )
        )

    def hole(self, claim: str, context: str, statement: str = "") -> str:
        """A tactic block for one subgoal of a skeleton."""
        return strip_fence(
            self._ask(
                HOLE_PROMPT.format(
                    claim=claim,
                    context=context,
                    premises=self._premises(statement or claim),
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
