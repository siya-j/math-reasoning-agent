"""Theorem proving (Prover Agent, arXiv 2506.19923, §3).

    goal in English
      -> formalise                     statement in Lean
      -> direct proving                informally guided        (xPROOF_ATTEMPTS)
      -> refinement                    driven by compiler errors (xPROOF_REFINEMENTS)
      -> auxiliary lemmas              bottom-up strategy search (depth LEMMA_DEPTH)
      -> synthesis                     final proof from proved lemmas
      -> ProofRun

Same shape as pipeline.run(): the loop lives in code, the judgment lives in
the model. Retrying and decomposing are guaranteed here rather than hoped for
in a prompt, for the reason measured in Phase 4 — a model asked to iterate
often simply does not.

WHY LEMMAS MAY AFFECT THE OUTCOME HERE
--------------------------------------
In pipeline.py, auxiliary checks are evidence and can never move the verdict:
SymPy confirming three special cases says nothing about the general claim.

Here, proved lemmas ARE allowed to contribute — because the assembled proof
is submitted to the compiler. The lemmas are inputs to something that gets
checked, not evidence trusted on its own. Remove the final verification step
and this becomes unsound immediately.
"""

from __future__ import annotations

import config
from domain.proof import Lemma, ProofAttempt, ProofRun, ProofStage  # noqa: F401
from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from llm.formalizer import Formalizer
from pipeline.skeleton import fill_hole, hole_claims, hole_count, summarise
from pipeline.tactics import cheap_attempt
from verifiers import verify as verify_request


def lean_check(statement: str, proof: str) -> Verdict:
    """Ask the verifier registry to decide a formal claim."""
    return verify_request(
        VerificationRequest(
            kind=VerificationKind.FORMAL, statement=statement, proof=proof
        )
    )


def lean_structure_ok(statement: str, proof: str) -> bool:
    """Does this proof TYPECHECK, even if it still contains `sorry`?

    This is the skeleton signal. A decomposition that compiles with holes has
    proved every `have` is well formed and that the final step follows from
    them — the remaining work is a set of independent subgoals.
    """
    from verifiers.lean_runner import LeanOutcome, run_lean
    from verifiers.lean_verifier import build_source

    result = run_lean(build_source(statement, proof))
    return result.outcome in (LeanOutcome.COMPILED, LeanOutcome.INCOMPLETE)


def _unknown(detail: str) -> Verdict:
    return Verdict(status=VerificationStatus.UNKNOWN, method="prover", detail=detail)


def prove(
    goal: str,
    formalizer: Formalizer | None = None,
    check=lean_check,
    depth: int | None = None,
    progress=None,
    reviewer=None,
    structure_check=None,
) -> ProofRun:
    """Attempt a formal proof of `goal`. Returns an explicit record either way.

    `formalizer` and `check` are injected so the entire strategy below can be
    exercised with no model and no Lean installation.

    `progress` is an optional callable invoked with a short stage name. A
    single goal can take minutes — mostly Lean reloading Mathlib — so a
    long-running proof needs to be distinguishable from a hung one.
    """
    formalizer = formalizer or Formalizer()
    depth = config.LEMMA_DEPTH if depth is None else depth
    structure_check = structure_check or lean_structure_ok

    def note(stage: str) -> None:
        if progress:
            progress(stage)

    run = ProofRun(goal=goal)

    # --- formalise --------------------------------------------------------
    note("formalising")
    run.statement = formalizer.statement(goal)
    if not run.statement.strip():
        run.verdict = _unknown("The claim could not be stated formally.")
        return run
    run.log("formalise", run.statement)

    # --- the mechanical attempt -------------------------------------------
    # One Lean compile, no model call. Every outcome measured so far turned on
    # retrieval rather than proof search, and once the right lemma is in hand
    # `exact <lemma>` usually closes the goal. Trying that first is free
    # compared with a sketch-and-generate round trip.
    note("standard tactics")
    premises = getattr(formalizer, "premises_for", lambda _: [])(run.statement)
    candidate = cheap_attempt(premises)
    if candidate:
        verdict = check(run.statement, candidate)
        run.record(
            ProofAttempt(len(run.attempts) + 1, ProofStage.CHEAP, candidate, verdict)
        )
        if verdict.status is VerificationStatus.TRUE:
            return _succeed(run, candidate, verdict, reviewer)

    note("sketching")
    sketch = formalizer.sketch(goal)

    # --- direct proving ---------------------------------------------------
    for attempt in range(config.PROOF_ATTEMPTS):
        note(f"direct {attempt + 1}/{config.PROOF_ATTEMPTS}")
        if _try(run, formalizer, check, ProofStage.DIRECT, sketch,
                reviewer=reviewer):
            return run

    # --- refinement on compiler feedback ---------------------------------
    # Prover Agent §3.1: refine the attempt with the FEWEST errors, not the
    # most recent one. The last attempt is often not the closest to correct.
    for attempt in range(config.PROOF_REFINEMENTS):
        note(f"refine {attempt + 1}/{config.PROOF_REFINEMENTS}")
        draft = best_draft(run)
        if _try(
            run,
            formalizer,
            check,
            ProofStage.REFINE,
            sketch,
            errors=draft.verdict.detail,
            previous=draft.proof,
            reviewer=reviewer,
        ):
            return run

    # --- skeleton, then fill the holes ------------------------------------
    # Decomposition WITHIN the proof, before decomposition across lemmas.
    # A skeleton that compiles with `sorry` has proved its decomposition
    # typechecks, which turns one hard goal into several independent easy
    # ones — each attacked mechanically before any model call is spent.
    note("skeleton")
    if _try_skeleton(run, formalizer, check, structure_check, sketch, note, reviewer):
        return run

    # --- auxiliary lemmas -------------------------------------------------
    if depth > 0:
        _gather_lemmas(run, formalizer, check, depth)

        if run.proved_lemmas:
            proofs = [
                f"{lemma.statement} := {lemma.proof}" for lemma in run.proved_lemmas
            ]
            candidate = formalizer.synthesis(run.statement, proofs)
            verdict = check(run.statement, candidate)
            run.record(
                ProofAttempt(
                    len(run.attempts) + 1, ProofStage.SYNTHESIS, candidate, verdict
                )
            )
            if verdict.status is VerificationStatus.TRUE:
                return _succeed(run, candidate, verdict, reviewer)

    return _give_up(run)


# ------------------------------------------------------------------ helpers
def best_draft(run: ProofRun) -> ProofAttempt:
    """The failed model-written attempt closest to compiling, by error count.

    Ties go to the earliest attempt, so the choice is deterministic.

    The mechanical `first | ...` attempt is excluded: it is not a draft
    anyone wrote, and asking a model to repair a list of alternatives it
    never proposed produces confusion rather than a fix.
    """
    drafts = [a for a in run.attempts if a.stage is not ProofStage.CHEAP]
    return min(
        drafts or run.attempts,
        key=lambda attempt: (attempt.error_count, attempt.number),
    )


def _try(run, formalizer, check, stage, sketch, errors="", previous="",
         reviewer=None) -> bool:
    """One proposal-and-check cycle. True if the compiler accepted it."""
    candidate = formalizer.proof(
        run.statement, sketch, errors=errors, previous=previous
    )
    verdict = check(run.statement, candidate)
    run.record(ProofAttempt(len(run.attempts) + 1, stage, candidate, verdict))

    if verdict.status is VerificationStatus.TRUE:
        _succeed(run, candidate, verdict, reviewer)
        return True
    return False


def _try_skeleton(run, formalizer, check, structure_check, sketch, note,
                  reviewer) -> bool:
    """Decompose, verify the decomposition, then discharge each hole."""
    # Optional capability: a formalizer that cannot decompose simply skips
    # this stage, exactly as one without retrieval skips premises.
    make_skeleton = getattr(formalizer, "skeleton", None)
    if make_skeleton is None:
        return False

    proof = make_skeleton(run.statement, sketch, config.SKELETON_STEPS)
    if not hole_count(proof):
        return False   # not a skeleton; the direct path already covered this

    run.log("skeleton", summarise(proof))

    # The decomposition itself must typecheck. Filling holes in a skeleton
    # that does not compile is wasted effort, and the failure is structural
    # rather than something a subgoal tactic could repair.
    if not structure_check(run.statement, proof):
        run.log("skeleton", "rejected: the decomposition does not typecheck")
        return False

    claims = hole_claims(proof)
    premises = getattr(formalizer, "premises_for", lambda _: [])(run.statement)

    for index, claim in enumerate(claims[: config.MAX_HOLES]):
        note(f"hole {index + 1}/{min(len(claims), config.MAX_HOLES)}")

        # Mechanical first: a subgoal is exactly the size `simp` or a cited
        # premise tends to close, and it costs no model call.
        candidate = fill_hole(proof, index, cheap_attempt(premises))
        if structure_check(run.statement, candidate):
            proof = candidate
            continue

        fill = getattr(formalizer, "hole", None)
        if claim and fill:
            filled = fill_hole(proof, index, f"by {fill(claim, proof, run.statement)}")
            if structure_check(run.statement, filled):
                proof = filled

    verdict = check(run.statement, proof)
    run.record(ProofAttempt(len(run.attempts) + 1, ProofStage.SKELETON, proof, verdict))
    if verdict.status is VerificationStatus.TRUE:
        _succeed(run, proof, verdict, reviewer)
        return True
    return False


def _gather_lemmas(run, formalizer, check, depth) -> None:
    """Propose auxiliary facts and try to prove each one on its own."""
    for informal in formalizer.lemmas(run.goal, config.MAX_LEMMAS):
        # Recursion: a lemma is just a smaller goal. Depth is bounded so a
        # hard problem cannot spawn work without limit.
        sub = prove(informal, formalizer=formalizer, check=check, depth=depth - 1)
        run.lemmas.append(
            Lemma(
                informal=informal,
                statement=sub.statement,
                proof=sub.proof,
                verdict=sub.verdict,
            )
        )
    run.log(
        "lemmas", f"{len(run.proved_lemmas)}/{len(run.lemmas)} proved"
    )


def _succeed(run: ProofRun, proof: str, verdict: Verdict, reviewer=None) -> ProofRun:
    """Record a compiler-accepted proof, then let review DOWNGRADE it.

    Review runs only here, on success, because that is the only point where
    the system is about to claim something. It can move TRUE to UNKNOWN and
    can do nothing else — see llm/reviewer.py for why granting approval would
    be unsafe.
    """
    run.proof = proof
    run.verdict = verdict

    if reviewer is None:
        return run

    run.review = reviewer.review(run.goal, run.statement)
    if run.review.objected:
        run.log("review", "objected")
        run.verdict = Verdict(
            status=VerificationStatus.UNKNOWN,
            method="reviewer",
            detail=(
                "Lean accepted a proof, but the formal statement may not match "
                f"the question asked: {run.review.concerns[0]} "
                "The proof is recorded; the claim is not treated as settled."
            ),
        )
    return run


def _give_up(run: ProofRun) -> ProofRun:
    """Report failure honestly, including anything that was proved on the way."""
    proved = len(run.proved_lemmas)
    detail = (
        f"No proof found in {len(run.attempts)} attempt(s). "
        "Failure to find a proof is not evidence that the claim is false."
    )
    if proved:
        detail += f" {proved} auxiliary lemma(s) were proved and are recorded."
    run.verdict = _unknown(detail)
    return run
