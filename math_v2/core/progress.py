"""Reading the record back — what the agent has actually established so far.

NO `from __future__ import annotations` (blueprint §5.1, gotcha 1).

WHY THIS EXISTS
---------------
Until now the proof record was write-only from the agent's side: sixteen tools
wrote to it and only `finish` ever read it. The agent's memory of its own work
was therefore the conversation alone — and in production
`CappedSummarizationMiddleware` compacts the conversation once it grows.

That undercuts the three things this system is supposed to do. Reflection needs
the failures and their errors. Decomposition needs the holes still outstanding.
Synthesis needs the lemmas already proved, by name, so they can be cited.
`ProofStage.SYNTHESIS` in the old prover existed precisely to re-present proved
lemmas at the end; here that information simply had no reader.

NO NEW MATHEMATICS
------------------
Everything below is a projection of `proof_log.json`. Nothing is computed,
inferred, retried or decided. If a fact is not in the record because a tool did
not run, it does not appear here — the point is to reflect execution, not to
supplement it.
"""

from math_v2.core import budget, log
from pipeline.skeleton import hole_claims

MAX_FAILURES = 6
MAX_DETAIL = 400


def _dedupe(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def failed_attempts(workdir):
    """Rejected attempts at the goal, most recent last, with what Lean said."""
    out = []
    for record in log.records(workdir, log.PROOF):
        if record.get("status") == log.TRUE:
            continue
        out.append({
            "proof": record.get("proof", ""),
            "error": (record.get("detail", "") or "")[:MAX_DETAIL],
        })
    return out[-MAX_FAILURES:]


def open_holes(workdir):
    """Claims left as `sorry` by the most recent skeleton that typechecked.

    Only a typechecking skeleton counts: one that did not typecheck describes a
    decomposition that does not combine into the goal, so its holes are not
    subgoals of anything.
    """
    for record in reversed(log.records(workdir, log.SKELETON)):
        if record.get("status") == log.TRUE:
            return hole_claims(record.get("proof", ""))
    return []


def computations(workdir):
    """Symbolic results so far — the evidence, never the verdict."""
    return [
        {"call": r.get("statement", ""), "result": r.get("status", ""),
         "detail": (r.get("detail", "") or "")[:MAX_DETAIL]}
        for r in log.records(workdir, "computation")
    ]


def snapshot(workdir):
    """Everything the agent has established, as data."""
    data = log.read(workdir)
    spent = budget.summary(workdir)
    statement = log.current_goal(workdir)

    return {
        "statement": statement,
        "statement_checked": bool(log.records(workdir, log.STATEMENT_CHECK)),
        "proved": bool(log.accepted_proof(workdir, statement)),
        "lemmas_proved": log.kept_lemmas(workdir),
        "failed_attempts": failed_attempts(workdir),
        "open_holes": open_holes(workdir),
        "computations": computations(workdir),
        "premises_seen": _dedupe([p.get("name", "") for p in data["premises"]])[:20],
        "budget": spent,
    }


def render(state):
    """The snapshot as prose for the model. Facts only, and one nudge.

    The nudge is the single place this module says anything the record does not
    strictly contain, and it is limited to naming the option the agent has not
    taken. It cannot fabricate progress, because every claim around it is read
    from the log.
    """
    lines = []

    if state["statement"]:
        lines.append(f"Goal: {state['statement']}")
    else:
        lines.append("No formal statement is set yet.")

    if state["proved"]:
        lines.append("This goal is PROVED. Report it with `finish`.")

    lemmas = state["lemmas_proved"]
    if lemmas:
        lines.append(f"\nLemmas proved and available to cite ({len(lemmas)}):")
        lines.extend(f"  {text.splitlines()[0]}" for text in lemmas)
    else:
        lines.append("\nNo auxiliary lemmas proved yet.")

    holes = state["open_holes"]
    if holes:
        lines.append(f"\nSteps left as `sorry` in your last typechecking skeleton "
                     f"({len(holes)}):")
        lines.extend(f"  {index}. {claim}" for index, claim in enumerate(holes, 1))

    failures = state["failed_attempts"]
    if failures:
        lines.append(f"\nAttempts the compiler rejected ({len(failures)} most recent). "
                     "Do not resubmit these:")
        for attempt in failures:
            first = (attempt["proof"] or "").strip().splitlines()
            lines.append(f"  - {(first[0] if first else '')[:100]}")
            reason = (attempt["error"] or "").strip().splitlines()
            if reason:
                lines.append(f"      {reason[0][:120]}")

    if state["computations"]:
        lines.append("\nSymbolic results so far (evidence, not proof):")
        lines.extend(f"  {c['call']} -> {c['result']}" for c in state["computations"])

    if state["premises_seen"]:
        lines.append("\nMathlib names retrieved: "
                     + ", ".join(state["premises_seen"][:12]))

    spent = state["budget"]
    lines.append(
        f"\nSpent: {spent['lean_calls']} compilation(s), {spent['searches']} search(es), "
        f"{spent['symbolic_calls']} computation(s), {spent['seconds']:.0f}s."
    )

    if not state["proved"]:
        if failures and not lemmas and not holes:
            lines.append(
                "\nYou have tried the whole proof and it has been rejected. "
                "Consider decomposing: `try_skeleton` to check the shape of the "
                "argument, then `try_lemma` for each step."
            )
        elif holes and not lemmas:
            lines.append(
                "\nYour decomposition typechecks. Prove the steps above with "
                "`try_lemma`, then assemble them with `try_proof`."
            )
        elif lemmas:
            lines.append(
                "\nCite the lemmas above by name in your next `try_proof`."
            )

    return "\n".join(lines)
