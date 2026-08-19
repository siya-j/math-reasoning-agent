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

import asyncio
import re

from pipeline.skeleton import fill_hole, hole_claims
from pipeline.tactics import cheap_attempt
from retrieval.loogle import Premise, conclusion_of
from verifiers.lean_runner import has_placeholder
from verifiers.lean_verifier import build_source, declaration, interpret
from domain.verdict import VerificationStatus

from math_v2.core import binders, diagnosis, log

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


def already_tried(workdir, proof, statement, kind=log.PROOF):
    """The earlier rejected attempt identical to this one, or {}.

    `kind` because a repeated SKELETON costs exactly what a repeated proof
    costs. Measured on proofnet `exercise_1_26`: three of five attempts were
    the same decomposition with a `sorry` in the same place, and the guard was
    reading only `kind == PROOF`, so none of them was caught.
    """
    target = normalise(proof)
    for record in log.records(workdir, kind):
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


def _generic_already_failed(workdir, proof, statement):
    """Refuse a second bare closer once one has been rejected. None to proceed.

    MEASURED on the 4-goal run. `try_standard_tactics` compiles ~30 closers in
    ONE file — rfl, trivial, assumption, norm_num, decide, simp, simp_all,
    positivity, omega, linarith, aesop. The agent then spent separate compiles
    submitting `by aesop`, and `by rfl`, and a `have` chain ending in
    `trivial`. Lean had already answered all three, in the ladder, and said no.

    So this is not a judgement about the mathematics. It is arithmetic: the
    tactic is in the set the ladder ran, the ladder ran, the ladder failed.
    Compiling it alone cannot produce a different answer, and the budget is
    twelve compiles.
    """
    if not diagnosis.is_generic(proof):
        return None

    tried = [r for r in log.records(workdir, log.PROOF)
             if r.get("status") != log.TRUE
             and r.get("statement", "").strip() == (statement or "").strip()
             and diagnosis.is_generic(r.get("proof"))]
    if not tried:
        return None

    return {
        "ok": False,
        "error": "generic_exhausted",
        "outputs": {"accepted": False},
        "message": (
            "REFUSED, and not compiled: this is a generic closer, and a "
            "generic attempt on this goal has already been rejected. "
            "`try_standard_tactics` runs about thirty of them — rfl, simp, "
            "aesop, omega, linarith and the rest — inside a single "
            "compilation, so submitting one on its own cannot produce a new "
            "answer.\n\nThis goal needs an ARGUMENT. Read the goal state from "
            "the last rejection and do one of:\n"
            "  - cite a Mathlib lemma whose CONCLUSION is that goal "
            "(`search_mathlib` with `|- <shape>`), or\n"
            "  - `try_skeleton` a `have` per step of the mathematical "
            "argument, then `try_lemma` each hole, or\n"
            "  - if you believe the statement is false, `try_refutation`."
        ),
    }


def _premises(workdir):
    return [Premise(**entry) for entry in log.read(workdir)["premises"]]


def _says_nothing_refusal(statement):
    """Refuse a goal that has been weakened into triviality. Costs no compile."""
    return {
        "ok": False,
        "error": "trivial_conclusion",
        "outputs": {"elaborates": False},
        "message": (
            "REFUSED: this statement concludes `True`, which every proof "
            "closes and which asserts nothing. Compiling it would record a "
            "formalisation success for a claim that was thrown away.\n"
            "State the actual mathematical claim as the conclusion. If it will "
            "not elaborate, the elaboration error is the problem to solve — "
            "report `not_formalized` and say what Lean rejected, rather than "
            "weakening the theorem until it compiles."
        ),
    }


def seed_premises(workdir, statement, search):
    """Run the goal-shape query ladder ONCE, deterministically, for free.

    THE MACHINERY WAS ALREADY THERE AND NOTHING CALLED IT.
    `retrieval.loogle.premises_for` builds a ladder from the goal itself —
    hypothesis+conclusion, then conclusion patterns, then bare identifiers —
    and it is measurably better than the bare names a model types:

        IsCyclic                      2163 hits, wanted lemma not in first 200
        |- IsCyclic _                   54 hits, wanted lemma fourth
        Nat.card _ = _, IsCyclic _      10 hits, wanted lemma FIRST

    `pipeline/` used it. `math_v2` never did: its only premise source was
    whatever string the model passed to `search_mathlib`, which on the 4-goal
    run was `"constant"`, `"const"`, `"deriv"` — bare words, every time.

    So the ladder runs here, off the statement, the moment the statement is
    known to elaborate. No model call, no extra turn, and the premises are in
    the store before the agent's first search.
    """
    if search is None:
        return []
    try:
        found = search.premises_for(statement)
    except Exception:  # noqa: BLE001
        return []          # retrieval is an optimisation; it may never break a run


    found = [p for p in found if not _is_noise(p)]
    if found:
        log.remember_premises(workdir, [
            {"name": p.name, "type": p.type, "module": p.module, "doc": p.doc}
            for p in found
        ])
        log.note(workdir, "seeded from goal shape -> " + ", ".join(
            p.name for p in found[:8]))
    return found


def _is_noise(premise):
    """Imported lazily to keep this module free of the tool layer."""
    from math_v2.core.retrieval import is_noise

    return is_noise(premise)


async def check_statement(workdir, statement, run_lean, search=None):
    """Does the SIGNATURE elaborate? Checked with `sorry` as the proof.

    A goal naming an identifier Mathlib no longer has cannot be proved by
    anyone, and every compilation spent on it reports a proving failure for a
    formalisation fault. Measured on `lin-vector-space-basis`, where `Basis`
    had become `Module.Basis`.
    """
    if says_nothing(statement):
        log.append(workdir, log.Record(
            kind=log.STATEMENT_CHECK, statement=statement, status=log.FALSE,
            detail="The conclusion is `True`; the claim was thrown away.",
        ))
        return _says_nothing_refusal(statement)

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
        # OFF THE EVENT LOOP. `premises_for` is synchronous urllib walking a
        # ladder of up to twelve queries, so on a slow or unreachable Loogle it
        # would otherwise block the loop for up to twelve times LOOGLE_TIMEOUT.
        # Typical cost is one or two round trips: the ladder stops once
        # PREMISE_BUDGET is filled, and the first query usually fills it.
        seeded = await asyncio.to_thread(
            seed_premises, workdir, statement, search)
        listed = (
            "\n\nRetrieved from the SHAPE of your goal, before you asked:\n"
            + "\n".join(f"  {p.render()}" for p in seeded[:6])
            + "\n\nRead the signatures. If one CONCLUDES your goal, cite it."
            if seeded else ""
        )
        return {"ok": True, "outputs": {"elaborates": True,
                                        "premises": [p.name for p in seeded]},
                "message": "The statement elaborates. You can try to prove it."
                           + listed}
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


def _render_retrieved(found):
    if not found:
        return ""
    lines = "\n".join(f"  {p.render()}" for p in found[:5])
    return ("\n\nSEARCHED FOR YOU, from that error:\n" + lines
            + "\nRead the signatures. Use one of these rather than searching "
              "again for a word from the statement.")


async def _retrieve_for_failure(workdir, detail, search):
    """Run the query the ERROR implies, once, and hand back what it found.

    THE LOOP THIS CLOSES. Retrieval was seeded from the goal and then never
    driven by anything again: after a rejection the model chose its own query
    and the traces show what it chose — `"constant"`, `"deriv"`, `"abs"`,
    `"re"` — bare words returning Lean internals. Meanwhile the error itself
    named exactly what was missing.

    ONE query per rejected compile. That is bounded by MAX_LEAN_CALLS without
    any new counter, and it costs an HTTP lookup rather than a model turn.
    """
    query = diagnosis.retrieval_query(detail)
    if not query or search is None:
        return []
    try:
        found, _ = await asyncio.to_thread(search.search_with_suggestions, query)
    except Exception:  # noqa: BLE001
        return []

    from math_v2.core.retrieval import is_noise

    found = [p for p in found if not is_noise(p)][:5]
    if found:
        log.remember_premises(workdir, [
            {"name": p.name, "type": p.type, "module": p.module, "doc": p.doc}
            for p in found
        ])
        log.note(workdir, f"search (from error): {query!r} -> "
                          + ", ".join(p.name for p in found))
    return found


async def try_proof(workdir, statement, proof, run_lean, search=None,
                    repair=True):
    """Compile a candidate proof of the goal and report exactly what Lean said.

    An attempt already rejected is refused WITHOUT compiling. The failure this
    guards is measured: the stateless baseline emitted byte-identical proposals
    (attempts 2/3 identical, 4/5/6 identical), and a conversation is supposed to
    prevent that rather than being trusted to. Twenty seconds spent re-learning
    a known answer is twenty seconds not spent on a new idea.
    """
    # Cheapest checks first: regexes on the text, before any log scan.
    if has_placeholder(proof):
        return _placeholder_refusal()
    if says_nothing(statement):
        return _says_nothing_refusal(statement)

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

    # After the repeat guard: an exact repeat has a more specific diagnosis
    # than "this is generic", and both would otherwise fire on the same input.
    exhausted = _generic_already_failed(workdir, proof, statement)
    if exhausted:
        return exhausted

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

    # The error was always returned; what was missing was what to DO with it.
    # Measured: every rejection in the 4-goal run was answered with another
    # generic closer, whatever Lean had actually said.
    # ONE automatic repair, and only for the one failure mode the traces
    # actually show. `repair` is False on the recursive call, so a repair can
    # never trigger another.
    repair_compiles = 0
    if repair and diagnosis.classify(verdict.detail) is diagnosis.UNKNOWN_TACTIC:
        wrapped = diagnosis.exact_repair(proof)
        if wrapped and not already_tried(workdir, wrapped, statement):
            fixed = await try_proof(workdir, statement, wrapped, run_lean,
                                    search, repair=False)
            # Charged whether or not it worked: the compiler ran either way.
            repair_compiles = 1
            fixed.setdefault("outputs", {})["compiles_used"] = 1
            if fixed.get("outputs", {}).get("accepted"):
                fixed["message"] = (
                    "ACCEPTED after an automatic repair. Your term was correct; "
                    f"it needed `exact`. Compiled:\n\n    {wrapped}\n\n"
                    "The proof body sits inside `by ...`, so submit tactics — "
                    "`exact <term>` — not bare terms."
                ) + "\n\n" + fixed.get("message", "")
                return fixed
            # The repair failed too. Report the ORIGINAL rejection: the term
            # itself is what needs rethinking, not its wrapper.

    action = diagnosis.next_action(verdict.detail)
    found = await _retrieve_for_failure(workdir, verdict.detail, search)
    return {
        "ok": True,
        "outputs": {"accepted": False,
                    "failure": diagnosis.classify(verdict.detail),
                    "retrieved": [p.name for p in found],
                    "compiles_used": repair_compiles},
        "message": (f"REJECTED.\n{verdict.detail}"
                    + (f"\n\nWHAT THIS MEANS: {action}" if action else "")
                    + _render_retrieved(found)),
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


# `theorem foo` / `lemma foo`, and everything up to the top-level `:` is the
# binder list. Both are needed to turn a goal into its own negation.
_HEAD = re.compile(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][\w'.]*)\s*", re.MULTILINE)

# A goal that concludes `True` proves nothing. `by trivial` closes it, and the
# run then reports a formalisation success for a statement that says nothing.
_TRIVIAL = re.compile(r"^\(*\s*True\s*\)*$")


def split_signature(statement):
    """(name, binders, conclusion) for a Lean theorem, or ("", "", "").

    THE FIRST top-level `:`, not the last. `retrieval.loogle.conclusion_of`
    takes the last, which is right for its job (find what a lemma concludes)
    and wrong for this one: proofnet `exercise_1_26` concludes

        : ∃ c : ℂ, ∀ x, F₁ x = F₂ x + c

    and that inner `: ℂ` is also at bracket depth 0, so taking the last colon
    cuts the conclusion in half and produces `¬ (∀ ... : ∃ c, ℂ, ...)` — which
    is not Lean. Binders are bracketed; the first unbracketed colon ends them.
    """
    head = _HEAD.search(statement or "")
    if not head:
        return "", "", ""

    depth = 0
    for index in range(head.end(), len(statement)):
        character = statement[index]
        if character in "([{⟨":
            depth += 1
        elif character in ")]}⟩":
            depth -= 1
        elif character == ":" and depth == 0:
            if statement[index + 1:index + 2] == "=":
                break
            return (head.group(1),
                    statement[head.end():index].strip(),
                    statement[index + 1:].strip())
    return "", "", ""


def negation_of(statement):
    """The goal's own negation, as a Lean theorem. "" if it cannot be built.

    Mechanical, and that is the point. Asked to state a negation the model
    rewrites the binders from memory and gets one of them wrong; here they are
    the ORIGINAL text, moved inside a `¬ (∀ ...)` untouched:

        theorem ex {f : ℂ → ℂ} (Ω : Set ℂ) (h : IsOpen Ω) : f a = f b
        theorem ex_refutation : ¬ (∀ {f : ℂ → ℂ} (Ω : Set ℂ) (h : IsOpen Ω),
                                     f a = f b)

    Lean's `∀` accepts implicit and instance binders in exactly the form a
    theorem declares them, so no rewriting is needed and none is done. The
    hypotheses stay binders rather than becoming arrows for the same reason:
    every transformation is a chance to be wrong, and none is required.
    """
    name, binders, conclusion = split_signature(statement)
    if not name or not conclusion:
        return ""
    if not binders:
        return f"theorem {name}_refutation : ¬ ({conclusion})"
    return f"theorem {name}_refutation : ¬ (∀ {binders}, {conclusion})"


def says_nothing(statement):
    """Is the goal's conclusion trivially true?

    MEASURED on proofnet `exercise_1_19b`, where the model could not get the
    real claim past the elaborator and submitted this instead:

        theorem test (z : ℂ) (s : ℕ → ℂ) (h : s = ...) : True

    It elaborates, `by trivial` closes it, and the run scored a formalisation
    success. The claim being formalised was "the series converges on |z| = 1".
    Nothing whatever was established, and the number said otherwise — which is
    worse than the failure it replaced, because it is invisible.
    """
    _, _, conclusion = split_signature(statement)
    return bool(_TRIVIAL.match(conclusion))


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
    # The negation is derivable from the goal, so the agent is not required to
    # restate it — the binders are what it gets wrong, and a regex gets them
    # right by not touching them.
    statement = (statement or "").strip() or negation_of(log.current_goal(workdir))

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


# How many holes one skeleton may spend compiles on. A ten-`have` skeleton
# must not eat the twelve-compile budget, and the first few holes are where the
# decomposition either works or does not.
MAX_AUTO_FILLS = int(__import__("os").getenv("MRA_MAX_AUTO_FILLS", "3"))

# Claims not worth a compilation. `True` is closed by `trivial` and establishes
# nothing; a hole with no `have` (the final tactic of the proof) has no claim
# to prove on its own.
_TRIVIAL_CLAIM = re.compile(r"^\(*\s*(True|trivial)\s*\)*$")


def _normalise_claim(text):
    return " ".join((text or "").split())


def worth_proving(claim, statement, workdir):
    """Should this hole become a standalone lemma? Deterministic, no model.

    THE RULE IS NOT "IS IT USEFUL". Nothing here can know that. It is the
    narrower question of whether a compile spent on this claim could possibly
    tell us something, and there are exactly four ways it could not:

      * no claim at all — a `sorry` that is not a `have`, i.e. the final step
      * trivially true  — `True`, which `trivial` closes and which proves
                          nothing, the same collapse `says_nothing` guards
      * CIRCULAR        — the claim restates the goal's own conclusion, so
                          "proving" it is proving the goal by assuming it
      * already handled — kept, or already attempted and rejected

    The model still chooses the decomposition. This only declines to spend the
    compiler on claims whose answer is already known.
    """
    body = _normalise_claim(claim)
    if not body or _TRIVIAL_CLAIM.match(body):
        return False

    _, _, conclusion = split_signature(statement)
    if body == _normalise_claim(conclusion):
        return False

    if any(body in _normalise_claim(kept) for kept in log.kept_lemmas(workdir)):
        return False
    return not any(
        _normalise_claim(r.get("statement", "")).endswith(body)
        for r in log.records(workdir, log.LEMMA)
    )


async def synthesize_lemmas(workdir, statement, proof, run_lean, allowance):
    """Attempt each meaningful hole of a TYPECHECKED skeleton, deterministically.

    THE CONTROL-FLOW GAP THIS CLOSES
    --------------------------------
    `try_lemma` was registered, reachable and tested, and across the whole
    4-goal ProofNet run it was called ZERO times. Decomposition therefore ran
    half way: `exercise_1_26` produced four skeletons and proved not one of
    their holes. The tool was never the problem — nothing in the control flow
    ever turned a hole into an attempt, and a prompt asking more loudly is not
    a mechanism.

    So the holes are attempted here, by the CONTROLLER, using the deterministic
    tactic ladder that already exists. No model call is made: `cheap_attempt`
    builds one `first | ... | ...` from the standard closers plus every
    retrieved premise, which is the cheapest thing that could work and costs
    one compile per hole.

    WHAT IS AND IS NOT DECIDED HERE. The model wrote the claims; Lean
    typechecked the decomposition; this only tries to close what is left. A
    hole the ladder cannot close is handed back as work for the model, not
    silently dropped.

    Returns (proved, attempted, compiles_used).
    """
    proved = []
    attempted = []
    compiles = 0

    for index, claim in enumerate(hole_claims(proof)):
        if compiles >= allowance or len(log.kept_lemmas(workdir)) >= MAX_KEPT_LEMMAS:
            break
        if not worth_proving(claim, statement, workdir):
            continue

        name = f"mra_lemma_{index + 1}"
        # CONTEXT-AWARE. A hole of a real goal talks about the goal's own
        # objects — `Ω`, `f`, `F₁` — which do not exist outside the theorem.
        # Compiled standalone it could not elaborate, so decomposition fired
        # and achieved nothing. The binders the claim needs are copied in,
        # transitively closed, and no others.
        _, statement_binders, _ = split_signature(statement)
        lemma = binders.lemma_signature(name, statement_binders,
                                        _normalise_claim(claim))
        candidate = cheap_attempt(_premises(workdir))

        # Compiled with the lemmas already kept, and through the SAME
        # `interpret` as every other attempt — so `sorry`, `admit`, `axiom` and
        # `exact?` are rejected here exactly as they are everywhere else. There
        # is no second acceptance rule.
        result = await run_lean(
            build_source(full_statement(workdir, lemma), candidate))
        compiles += 1
        verdict = interpret(result, lemma)
        accepted = verdict.status is VerificationStatus.TRUE

        log.append(workdir, log.Record(
            kind=log.LEMMA, statement=lemma, proof=candidate,
            status=log.TRUE if accepted else log.FALSE, detail=verdict.detail,
        ))
        attempted.append({"name": name, "claim": _normalise_claim(claim),
                          "index": index, "accepted": accepted})
        if accepted:
            log.keep_lemma(workdir, declaration(lemma, candidate))
            proved.append({"name": name, "claim": _normalise_claim(claim),
                           "index": index})

    return proved, attempted, compiles


async def assemble(workdir, statement, proof, proved, run_lean):
    """Rebuild the skeleton with its holes cited and compile it. One compile.

    Only when EVERY hole was closed. A skeleton that typechecked plus lemmas
    for all of its holes is a complete proof, and finding that out costs one
    compilation and no model call — so the deterministic path can carry a goal
    all the way from decomposition to PROVED.
    """
    assembled = proof
    # DESCENDING. `fill_hole` counts `sorry` tokens in the CURRENT string, so
    # filling hole 0 first renumbers every hole after it and the second fill
    # silently misses. Measured on a two-hole skeleton: hole 1 stayed `sorry`,
    # the assembled proof was compiled anyway, and only the `sorry` anti-cheat
    # stopped it being read as a proof. Filling from the back leaves the
    # earlier indices untouched.
    for lemma in sorted(proved, key=lambda l: l["index"], reverse=True):
        assembled = fill_hole(
            assembled, lemma["index"],
            f"(first | exact {lemma['name']} "
            f"| apply {lemma['name']} <;> assumption "
            f"| exact {lemma['name']} (by assumption))")

    result = await run_lean(
        build_source(full_statement(workdir, statement), assembled))
    verdict = interpret(result, statement)

    log.append(workdir, log.Record(
        kind=log.PROOF, statement=statement, proof=assembled,
        status=_status(verdict), detail=verdict.detail,
    ))
    return verdict.status is VerificationStatus.TRUE, assembled


async def try_skeleton(workdir, statement, proof, run_lean, fill_budget=0):
    """Check that a decomposition holds together before filling it in.

    A skeleton that typechecks with `sorry` has proved the SHAPE of the
    argument: every step is well formed and the final step follows from them,
    so what remains is independent and smaller. It proves nothing on its own,
    and is recorded UNKNOWN whatever the compiler says.
    """
    if says_nothing(statement):
        return _says_nothing_refusal(statement)

    repeat = already_tried(workdir, proof, statement, kind=log.SKELETON)
    if repeat:
        return {
            "ok": False,
            "error": "duplicate_attempt",
            "outputs": {"typechecks": False},
            "message": (
                "REFUSED: this exact decomposition was already submitted and "
                "rejected, so it was not compiled again. Lean said:\n"
                + (repeat.get("detail", "") or "")[:600]
                + "\n\nChange the decomposition, or prove one of its holes "
                "with `try_lemma`."
            ),
        }

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

    # THE DECOMPOSITION IS NOW ACTED ON, NOT ANNOUNCED. Previously this
    # returned the hole list and asked the model to call `try_lemma`; across
    # four ProofNet goals it never did once. The holes are attempted here.
    proved, attempted, compiles = await synthesize_lemmas(
        workdir, statement, proof, run_lean, fill_budget)

    outstanding = [c for i, c in enumerate(claims)
                   if c and not any(p["index"] == i for p in proved)]

    assembled_ok = False
    if proved and not outstanding and compiles < fill_budget:
        assembled_ok, _ = await assemble(workdir, statement, proof, proved, run_lean)
        compiles += 1

    if assembled_ok:
        return {
            "ok": True,
            "outputs": {"typechecks": True, "holes": claims, "accepted": True,
                        "lemmas_proved": [p["name"] for p in proved],
                        "compiles_used": compiles},
            "message": (
                "ACCEPTED. Every hole was closed and the assembled proof "
                "compiles — the goal is PROVED. Report it with `finish`."
            ),
        }

    report = ""
    if attempted:
        report = "\n\nEach hole was then attempted automatically with the "
        report += "standard tactics and your retrieved premises:\n"
        report += "\n".join(
            f"  {a['name']}: {a['claim'][:80]} -> "
            + ("PROVED and kept, cite it by name" if a["accepted"]
               else "not closed; this one needs a real argument")
            for a in attempted
        )
    if outstanding:
        report += (
            "\n\nStill open: " + "; ".join(c[:60] for c in outstanding)
            + "\nProve one with `try_lemma`, then assemble with `try_proof`."
        )
    elif proved:
        report += "\n\nAll holes are proved. Assemble them with `try_proof`."

    return {
        "ok": True,
        "outputs": {"typechecks": True, "holes": claims, "accepted": False,
                    "lemmas_proved": [p["name"] for p in proved],
                    "outstanding": outstanding, "compiles_used": compiles},
        "message": (
            "The decomposition TYPECHECKS, so the steps do combine into the "
            "goal. What is left is independent and smaller:\n"
            f"{listed or '  (no holes found)'}"
            + report
            + "\n\nThe skeleton itself proves nothing."
        ),
    }
