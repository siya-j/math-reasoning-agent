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
from math_v2.core import log, verdict
from math_v2.tools._enums import OutcomeLit


@tool
async def finish(
    summary: str,
    outcome: OutcomeLit,
    runtime: ToolRuntime[MathContext],
    statement: str = "",
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
            verified_false | not_verified. Use `proved` only when `try_proof`
            reported ACCEPTED. A symbolic computation is never `proved`.
        statement: the formal statement you are claiming, when reporting a
            proof. Required for `proved` — it is what the record is matched
            against, so a proof of a different claim cannot be offered for
            this one.
    """
    workdir = runtime.context.workdir
    decision = verdict.proof_verdict(workdir, statement)

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

    refusal = verdict.refuse(outcome, decision)
    if refusal:
        return {
            "ok": True,
            "accepted": False,
            "outcome": decision["outcome"],
            "message": "REFUSED. " + refusal,
        }

    warnings = []
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
        "warnings": warnings,
    }
