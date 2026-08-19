"""Proof tool bodies. Portable: no Aura imports, no `@tool` decorator.

NO `from __future__ import annotations` (blueprint §5.1, gotcha 1).

WHAT THIS MODULE IS
-------------------
The logic of the five proving tools, as plain async functions. The `@tool`
wrapper that adds `ToolRuntime[MathContext]` and the CommandSpec dispatch is a
thin shell around each of these — so the reasoning is testable here, with no
container, no model and no framework, exactly as it was before the migration.

THE INJECTED SEAM
-----------------
Every function takes `run_lean`, an async callable `(source: str) ->
LeanResult`. That is the same shape as `verifiers/lean_runner.run_lean`, so:

  * locally and in tests, a subprocess or a fake
  * in Aura, a CommandSpec dispatched to math.sif

Nothing else changes between the two. Injection was already how the old prover
was tested; this keeps it.

REUSED WHOLESALE, NOT REWRITTEN
-------------------------------
`build_source` and `rename_goal` assemble the file (the model never writes it —
the `qe_v2` render-don't-let-the-model-write pattern). `interpret` decides what
the compiler's answer means. `cheap_attempt` builds the tactic ladder.
`hole_claims` reads a skeleton. All of it is existing, tested code.
"""

import re

from pipeline.skeleton import hole_claims
from pipeline.tactics import cheap_attempt
from retrieval.loogle import Premise, conclusion_of
from verifiers.lean_runner import has_placeholder
from verifiers.lean_verifier import build_source, declaration, interpret
from domain.verdict import VerificationStatus

from math_v2.core import log

# Kept lemmas grow the file that every later attempt must recompile, so this is
# a resource limit, not a limit on the agent's looping — that is the budget
# middleware's job.
MAX_KEPT_LEMMAS = 4


def _status(verdict):
    return {
        VerificationStatus.TRUE: log.TRUE,
        VerificationStatus.FALSE: log.FALSE,
    }.get(verdict.status, log.UNKNOWN)


def full_statement(workdir, statement):
    """The goal, preceded by every lemma already proved.

    Lean needs a declaration before it can be cited, and `rename_goal` renames
    the LAST declaration, so the lemmas keep the names the proof uses.
    """
    lemmas = log.kept_lemmas(workdir)
    return "\n\n".join(lemmas + [statement]) if lemmas else statement


def normalise(proof):
    """A proof's identity for repeat detection: whitespace is not a change.

    Deliberately crude. Reformatting the same tactic block is the same attempt;
    an agent that changes a variable name HAS changed something and is allowed
    to spend a compile finding out it did not help.
    """
    return " ".join((proof or "").split())


def already_tried(workdir, proof, statement):
    """The earlier rejected attempt identical to this one, or {}."""
    target = normalise(proof)
    for record in log.records(workdir, log.PROOF):
        if record.get("status") == log.TRUE:
            continue
        if record.get("statement", "").strip() != (statement or "").strip():
            continue
        if normalise(record.get("proof")) == target:
            return record
    return {}


def _placeholder_refusal():
    """Refuse `sorry`/`admit` without compiling.

    Lean ACCEPTS both, so the answer is knowable from the text and the compile
    teaches nothing. Measured on proofnet `exercise_1_13a`: attempt 2 of 3 was
    the single word `sorry`, and it cost a REPL round-trip and a third of the
    budget. Shaped like `duplicate_attempt` — nothing is logged, so a refusal
    is not an attempt.

    `try_skeleton` is exempt: holes are the point there.
    """
    return {
        "ok": False,
        "error": "placeholder_proof",
        "outputs": {"accepted": False},
        "message": (
            "REFUSED: this contains `sorry` or `admit`, so it was not "
            "compiled. Lean accepts both and they prove nothing. If you "
            "cannot close the goal outright, decompose it — `try_skeleton` is "
            "where holes belong, and `try_lemma` proves them one at a time."
        ),
    }


def _premises(workdir):
    return [Premise(**entry) for entry in log.read(workdir)["premises"]]


async def check_statement(workdir, statement, run_lean):
    """Does the SIGNATURE elaborate? Checked with `sorry` as the proof.

    A goal naming an identifier Mathlib no longer has cannot be proved by
    anyone, and every compilation spent on it reports a proving failure for a
    formalisation fault. Measured on `lin-vector-space-basis`, where `Basis`
    had become `Module.Basis`.
    """
    result = await run_lean(build_source(statement, "sorry"))
    verdict = interpret(result, statement)

    # INCOMPLETE means "compiles, but uses sorry" — which is exactly what a
    # well-formed signature with a placeholder proof should do. The signature
    # is only broken when the compiler could not get that far.
    elaborates = verdict.status is not VerificationStatus.UNKNOWN or (
        "sorry" in verdict.detail or "admit" in verdict.detail
    )

    log.append(workdir, log.Record(
        kind=log.STATEMENT_CHECK, statement=statement,
        status=log.TRUE if elaborates else log.FALSE, detail=verdict.detail,
    ))

    if elaborates:
        return {"ok": True, "outputs": {"elaborates": True},
                "message": "The statement elaborates. You can try to prove it."}
    return {
        "ok": True,
        "outputs": {"elaborates": False, "detail": verdict.detail},
        "message": (
            "Lean cannot make sense of this STATEMENT, so no proof of it can "
            "compile. The fault is in the signature, not in any proof.\n"
            f"{verdict.detail}\n"
            "Fix names and notation — Mathlib renames things — without "
            "changing what the statement says."
        ),
    }


async def try_proof(workdir, statement, proof, run_lean):
    """Compile a candidate proof of the goal and report exactly what Lean said.

    An attempt already rejected is refused WITHOUT compiling. The failure this
    guards is measured: the stateless baseline emitted byte-identical proposals
    (attempts 2/3 identical, 4/5/6 identical), and a conversation is supposed to
    prevent that rather than being trusted to. Twenty seconds spent re-learning
    a known answer is twenty seconds not spent on a new idea.
    """
    # Cheapest check first: a regex on the candidate, before the log scan.
    if has_placeholder(proof):
        return _placeholder_refusal()

    repeat = already_tried(workdir, proof, statement)
    if repeat:
        return {
            "ok": False,
            "error": "duplicate_attempt",
            "outputs": {"accepted": False},
            "message": (
                "REFUSED: this exact proof was already submitted and rejected, "
                "so it was not compiled again. Lean said:\n"
                + (repeat.get("detail", "") or "")[:600]
                + "\n\nChange the approach rather than the formatting. If you "
                "are out of ideas for the whole proof, call `proof_state` to see "
                "what you have, then decompose with `try_skeleton` and "
                "`try_lemma`."
            ),
        }

    source = build_source(full_statement(workdir, statement), proof)
    result = await run_lean(source)
    verdict = interpret(result, statement)

    log.append(workdir, log.Record(
        kind=log.PROOF, statement=statement, proof=proof,
        status=_status(verdict), detail=verdict.detail,
    ))

    if verdict.status is VerificationStatus.TRUE:
        return {"ok": True, "outputs": {"accepted": True},
                "message": "ACCEPTED. The proof compiles. Report it with `finish`."}
    return {
        "ok": True,
        "outputs": {"accepted": False},
        "message": f"REJECTED.\n{verdict.detail}",
    }


async def try_standard_tactics(workdir, statement, run_lean):
    """The usual closers and every retrieved premise, in ONE compile.

    Lean's `first | t1 | t2 | ...` commits to the first alternative that
    closes the goal, so thirty candidates cost one invocation rather than
    thirty.
    """
    candidate = cheap_attempt(_premises(workdir))
    source = build_source(full_statement(workdir, statement), candidate)
    result = await run_lean(source)
    verdict = interpret(result, statement)

    log.append(workdir, log.Record(
        kind=log.PROOF, statement=statement, proof=candidate,
        status=_status(verdict), detail=verdict.detail,
    ))

    if verdict.status is VerificationStatus.TRUE:
        return {"ok": True, "outputs": {"accepted": True},
                "message": "ACCEPTED. A standard tactic closed the goal."}
    return {
        "ok": True,
        "outputs": {"accepted": False},
        "message": (
            "None of the standard tactics closed it. You will need to write "
            f"the proof.\n{verdict.detail[:600]}"
        ),
    }


async def try_lemma(workdir, statement, proof, run_lean, limit=MAX_KEPT_LEMMAS):
    """Prove a helper result and keep it if the compiler accepts.

    A kept lemma is cited by name in everything written afterwards. It is
    recorded as `kind=LEMMA`, which is what stops the guard reading a helper's
    success as the goal's.
    """
    if has_placeholder(proof):
        return _placeholder_refusal()

    kept = log.kept_lemmas(workdir)
    if len(kept) >= limit:
        return {
            "ok": True,
            "outputs": {"accepted": False, "kept": len(kept)},
            "message": (
                f"Lemma budget spent ({limit} kept). Use the ones you have "
                "and prove the goal."
            ),
        }

    # Compiled against the lemmas already kept, so a helper may build on an
    # earlier helper.
    combined = "\n\n".join(kept + [statement]) if kept else statement
    result = await run_lean(build_source(combined, proof))
    verdict = interpret(result, statement)

    log.append(workdir, log.Record(
        kind=log.LEMMA, statement=statement, proof=proof,
        status=_status(verdict), detail=verdict.detail,
    ))

    if verdict.status is not VerificationStatus.TRUE:
        return {
            "ok": True,
            "outputs": {"accepted": False},
            "message": f"The lemma was REJECTED, so it has not been kept.\n{verdict.detail}",
        }

    log.keep_lemma(workdir, declaration(statement, proof))
    return {
        "ok": True,
        "outputs": {"accepted": True, "kept": len(kept) + 1},
        "message": (
            "ACCEPTED and kept. You may cite it by name in any later attempt. "
            f"Lemmas held: {len(kept) + 1}. This proves the LEMMA, not the "
            "goal — the goal still needs `try_proof`."
        ),
    }


# A refutation must CONCLUDE a negation. Without this the tool would accept a
# proof of any true theorem — `2 + 2 = 4` compiles, and would otherwise be
# recorded as having refuted the goal.
_NEGATION = re.compile(r"¬|≠|\bNot\b|(?:→|->)\s*False\s*$")


def negates(statement):
    """Does this theorem's CONCLUSION state a negation?

    A LINT, and deliberately a weak one. It establishes that the agent is
    claiming a negation, not that the negation is of THIS goal — that would
    need the two statements related inside Lean, which is a second proof system
    and not what this is for. The goal is recorded next to the refutation so a
    reader can see both, and the honest limit is written into the outcome name:
    a refutation is verified, the CORRESPONDENCE is reviewed.
    """
    return bool(_NEGATION.search(conclusion_of(statement) or statement or ""))


async def try_refutation(workdir, statement, proof, run_lean):
    """Prove the NEGATION of the goal — the only way a statement becomes refuted.

    WHY THIS EXISTS
    ---------------
    `statement_suspect` was the one terminal claim in this system checked
    against prose rather than against a compilation. Measured on proofnet
    `exercise_1_13a`: the agent was RIGHT — the ProofNet port of Stein &
    Shakarchi 1.13a drops connectedness, so f can be a different constant on
    each component — and it had the counterexample, Ω = D(0,1) ∪ D(3,1). It
    never formalised it, so the finding was recorded as prose and the run
    scored as a failure to prove.

    This routes that claim through the same compiler, the same `interpret`, and
    therefore the same anti-cheat as every other proof: `sorry` and `admit` are
    refused before dispatch, and a file that only compiles via `axiom` or
    `exact?` comes back CHEATED, not TRUE. There is no path to REFUTED that
    does not go through a proof Lean accepted.
    """
    if has_placeholder(proof):
        return _placeholder_refusal()

    if not negates(statement):
        return {
            "ok": False,
            "error": "not_a_negation",
            "outputs": {"refuted": False},
            "message": (
                "REFUSED: this statement does not conclude a negation, so "
                "proving it would not refute anything. State the negation "
                "explicitly — `¬ (∀ ...)`, `... ≠ ...` — and prove THAT."
            ),
        }

    # Compiled against the kept lemmas, like every other attempt. A
    # counterexample is a CONSTRUCTION — the function, then its properties —
    # and it is the last thing that should be forced into one declaration.
    # `rename_goal` renames only the last, so the lemmas keep the names the
    # refutation cites.
    result = await run_lean(build_source(full_statement(workdir, statement), proof))
    verdict = interpret(result, statement)
    refuted = verdict.status is VerificationStatus.TRUE

    log.append(workdir, log.Record(
        kind=log.REFUTATION, statement=statement, proof=proof,
        status=log.TRUE if refuted else log.FALSE, detail=verdict.detail,
    ))

    if not refuted:
        return {
            "ok": True,
            "outputs": {"refuted": False},
            "message": (
                "The negation was NOT established, so the statement stays "
                f"unproved rather than refuted.\n{verdict.detail}"
            ),
        }
    return {
        "ok": True,
        "outputs": {"refuted": True},
        "message": (
            "REFUTED. Lean accepted a proof of the negation, so the goal is "
            "false as written. Report it with `finish` as `statement_suspect` "
            "— the compiled refutation is what upgrades that to a verified "
            "result. Say in the summary which hypothesis is missing."
        ),
    }


async def try_skeleton(workdir, statement, proof, run_lean):
    """Check that a decomposition holds together before filling it in.

    A skeleton that typechecks with `sorry` has proved the SHAPE of the
    argument: every step is well formed and the final step follows from them,
    so what remains is independent and smaller. It proves nothing on its own,
    and is recorded UNKNOWN whatever the compiler says.
    """
    source = build_source(full_statement(workdir, statement), proof)
    result = await run_lean(source)
    verdict = interpret(result, statement)

    # TRUE means it compiled outright; UNKNOWN-with-sorry means it typechecked
    # with holes. Both are a well-formed decomposition.
    holds = verdict.status is VerificationStatus.TRUE or "sorry" in verdict.detail

    log.append(workdir, log.Record(
        kind=log.SKELETON, statement=statement, proof=proof,
        status=log.TRUE if holds else log.FALSE, detail=verdict.detail,
    ))

    if not holds:
        return {
            "ok": True,
            "outputs": {"typechecks": False},
            "message": (
                "The decomposition does NOT typecheck, so the steps do not yet "
                f"combine into the goal.\n{verdict.detail}"
            ),
        }

    claims = hole_claims(proof)
    listed = "\n".join(f"  {i + 1}. {claim}" for i, claim in enumerate(claims))
    return {
        "ok": True,
        "outputs": {"typechecks": True, "holes": claims},
        "message": (
            "The decomposition TYPECHECKS, so the steps do combine into the "
            "goal. What is left is independent and smaller:\n"
            f"{listed or '  (no holes found)'}\n"
            "Prove these with `try_lemma`, then submit the assembled proof "
            "with `try_proof`. The skeleton itself proves nothing."
        ),
    }
