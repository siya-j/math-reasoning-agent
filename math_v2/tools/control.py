"""`finish` — the honesty guard. THE reason this system can be trusted.

NO `from __future__ import annotations` (§5.1, gotcha 1).

A DELIBERATE DIVERGENCE FROM THE HOUSE PATTERN
----------------------------------------------
AGENT_BLUEPRINT.md §5.5 calls `finish` "framework-optional": a deep agent
terminates fine when the model stops calling tools, and `mach_v2` documents its
own as "known scaffolding, not validated-correct".

**For this agent it is mandatory and it is validated.** Everything else here is
mechanics; this is the invariant the whole architecture exists to protect:

    the agent may claim anything in prose
    -> the verdict is computed from RECORDED tool executions
    -> a claim no record supports is REFUSED, and the model is told why

If a future refactor makes this optional, or lets it accept the model's own
account of what happened, the system stops being a verifier and becomes a
language model with extra steps. `chem_v2`'s version validates that claimed
artefacts exist on disk; this validates that a claimed proof was compiled.
Same idea, harder subject.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from math_v2 import _aura
from math_v2.context import MathContext
from math_v2.core import budget, log, verdict
from math_v2.tools._enums import OutcomeLit


@tool
async def finish(
    summary: str,
    outcome: OutcomeLit,
    runtime: ToolRuntime[MathContext],
    statement: str = "",
    claim: str = "",
) -> dict:
    """Report the result. The record decides whether your claim is allowed.

    Call this once you are done, whatever happened — including when nothing
    could be settled. A claim of "proved" is checked against the compilations
    actually recorded, and REFUSED if none of them accepted this statement.
    Being refused is not a failure of the turn: correct the outcome and call
    again.

    Args:
        summary: what you did and what you concluded, for the reader.
        outcome: proved | not_proved | not_formalized | verified_true |
            verified_false | not_verified | statement_suspect. Use `proved`
            only when `try_proof` reported ACCEPTED. A symbolic computation is
            never `proved`. Use `statement_suspect` when the theorem looks
            false or ill-posed AS WRITTEN — say why in the summary. That is
            recorded as your report, not accepted as fact, and it keeps a
            broken statement from counting against the proof rate.
        statement: the formal statement you are claiming, when reporting a
            proof. Required for `proved` — it is what the record is matched
            against, so a proof of a different claim cannot be offered for
            this one.
        claim: the user's original question, in their words. Used to check
            that the theorem proved is the one that was asked about. Strongly
            recommended for `proved`: a proof of the wrong theorem is more
            convincing than no proof at all, not less.
    """
    workdir = runtime.context.workdir
    decision = verdict.proof_verdict(workdir, statement)

    # Recorded in the trace, where the evaluator reads it. Never a verdict:
    # the guard still reports the goal as unproved, and this only stops a
    # benchmark row we cannot trust being scored against the prover.
    #
    # It is GUARDED like a verdict even so, because it ends the run. Measured:
    # three of four ProofNet goals took this exit having compiled no proof at
    # all, so `genuinely_tested` was 0 of 4. Requiring one rejected attempt
    # makes the report cost the same as trying, which removes the incentive to
    # prefer it. See verdict.suspect_refusal.
    if outcome == verdict.STATEMENT_SUSPECT:
        unearned = verdict.suspect_refusal(workdir)
        if unearned:
            return {
                "ok": True,
                "accepted": False,
                "outcome": verdict.NOT_PROVED,
                "error": "suspect_unearned",
                "message": "REFUSED. " + unearned,
                "budget": budget.summary(workdir),
            }
        # Two different things share this exit, and only one of them is a
        # result. A compiled proof of the negation makes the report a VERDICT;
        # without one it stays a diagnostic, and the goal is still scored as
        # unproved. The agent cannot choose between them — the record does.
        refutation = verdict.verified_refutation(workdir)
        if refutation:
            outcome = verdict.REFUTED
            log.note(workdir, f"refuted statement: {summary[:300]}")
            # Without this the report says "refuted" and nothing says of what.
            decision["evidence"] = {
                **decision.get("evidence", {}),
                "refutation": {
                    "statement": refutation.get("statement", ""),
                    "proof": refutation.get("proof", ""),
                },
            }
        else:
            log.note(workdir, f"suspect statement: {summary[:300]}")

    if outcome == verdict.PROVED and not statement.strip():
        return {
            "ok": False,
            "accepted": False,
            "error": "statement_required",
            "message": (
                "To report a proof you must give the formal `statement` it "
                "proves, so it can be checked against what was compiled."
            ),
        }

    # A compiled proof of the WRONG theorem is failures 3 and 8 with a proof
    # assistant attached. Lean guarantees the statement is true; nothing
    # guarantees it is the statement the user asked about, so the lint runs
    # here, on the accepted statement, before anything is reported.
    if outcome == verdict.PROVED:
        proved = decision.get("evidence", {}).get("statement", statement)
        unfaithful = verdict.faithfulness_failure(proved, claim)
        if unfaithful:
            return {
                "ok": True,
                "accepted": False,
                "outcome": verdict.NOT_VERIFIED,
                "error": "unfaithful_statement",
                "message": "REFUSED. " + unfaithful,
                "budget": budget.summary(workdir),
            }

    refusal = verdict.refuse(outcome, decision)
    if refusal:
        return {
            "ok": True,
            "accepted": False,
            "outcome": decision["outcome"],
            "message": "REFUSED. " + refusal,
            "budget": budget.summary(workdir),
        }

    warnings = []
    spent = budget.summary(workdir)
    if spent["terminated_early"]:
        # Ran out rather than finished. Different results, and conflating them
        # would misreport a proof rate.
        warnings.append(f"Stopped early: {spent['reason']}.")

    dropped = _aura.dropped_fields()
    if dropped:
        # Not cosmetic. A dropped `timeout` means a runaway compile was bounded
        # by something other than us; a dropped `stdin` means every symbolic op
        # received empty arguments.
        warnings.append(
            "This installation's CommandSpec does not accept: "
            + ", ".join(dropped)
            + ". Those requests were not made."
        )

    return {
        "ok": True,
        "accepted": True,
        "outcome": outcome,
        "banner": verdict.BANNERS.get(outcome, ""),
        "summary": summary,
        "evidence": decision.get("evidence", {}),
        "record": {
            "attempts": len(log.records(workdir, log.PROOF)),
            "lemmas_kept": len(log.kept_lemmas(workdir)),
            "computations": len(log.records(workdir, "computation")),
        },
        "budget": spent,
        "warnings": warnings,
    }
