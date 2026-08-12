"""The guard. A verdict computed from records, never from what the agent says.

NO `from __future__ import annotations` (blueprint §5.1).

THIS IS THE REASON THE SYSTEM IS TRUSTWORTHY
--------------------------------------------
Everything else in the migration is mechanics. This is the invariant:

    the agent may claim anything in prose
    -> the VERDICT is computed from RECORDED tool executions
    -> a claim no record supports is refused

AGENT_BLUEPRINT.md §5.5 calls `finish` "framework-optional" — a deep agent
terminates fine when the model stops calling tools. **For this agent it is
mandatory**, because a natural stop would let the model end a turn asserting a
proof that never compiled. That is a deliberate divergence from the house
pattern, and it is written down here rather than left for someone to infer.

WHY A FAILED PROOF IS NEVER `false`
-----------------------------------
Lean failing to compile a proof of P is not evidence against P. It almost
always means the proof was wrong, or Mathlib spells the lemma differently, or
the statement was mistranscribed. Reading failure as refutation would make this
system declare true theorems false, which is worse than having no prover at
all. So proving yields TRUE or UNKNOWN and nothing else.

SymPy is different: it decides computations, and may return FALSE. But a SymPy
result may never establish a PROOF — on the proving path it informs the model
and stops there. Both rules are enforced below rather than requested in a
prompt.
"""

from math_v2.core import log

# What `finish` is allowed to conclude.
PROVED = "proved"
VERIFIED_TRUE = "verified_true"
VERIFIED_FALSE = "verified_false"
NOT_PROVED = "not_proved"
NOT_FORMALIZED = "not_formalized"
NOT_VERIFIED = "not_verified"

BANNERS = {
    PROVED: "PROVED (Lean accepted a complete proof)",
    VERIFIED_TRUE: "VERIFIED TRUE",
    VERIFIED_FALSE: "VERIFIED FALSE",
    NOT_PROVED: "NOT PROVED",
    NOT_FORMALIZED: "NOT FORMALISED",
    NOT_VERIFIED: "NOT VERIFIED",
}


def proof_verdict(workdir: str, statement: str = "") -> dict:
    """Decide a proof claim from the log alone.

    Returns {"outcome", "banner", "reason", "evidence"}.
    """
    accepted = log.accepted_proof(workdir, statement)
    if accepted:
        return {
            "outcome": PROVED,
            "banner": BANNERS[PROVED],
            "reason": "The compiler accepted this proof.",
            "evidence": {
                "statement": accepted.get("statement", ""),
                "proof": accepted.get("proof", ""),
                "detail": accepted.get("detail", ""),
            },
        }

    attempts = log.records(workdir, log.PROOF)
    checks = log.records(workdir, log.STATEMENT_CHECK)

    # A statement Lean cannot elaborate is a FORMALISATION failure. Counting it
    # as "not proved" credits the formalisation with a success it did not have
    # and blames the prover for a proof that could never have existed.
    if checks and all(c.get("status") == log.FALSE for c in checks) and not attempts:
        return {
            "outcome": NOT_FORMALIZED,
            "banner": BANNERS[NOT_FORMALIZED],
            "reason": (
                "Lean could not make sense of the formal statement, so the "
                "claim was never put to the prover."
            ),
            "evidence": {"detail": checks[-1].get("detail", "")},
        }

    lemmas = len(log.kept_lemmas(workdir))
    return {
        "outcome": NOT_PROVED,
        "banner": BANNERS[NOT_PROVED],
        "reason": (
            f"No accepted compilation of the goal in {len(attempts)} attempt(s)"
            + (f"; {lemmas} helper lemma(s) were proved" if lemmas else "")
            + ". Failure to find a proof is not evidence that the claim is false."
        ),
        "evidence": {"attempts": len(attempts), "lemmas": lemmas},
    }


def refuse(claim_outcome: str, verdict: dict) -> str:
    """Why a claimed outcome is not supported. "" when it is supported.

    Called by `finish` before it accepts anything the model asserts.
    """
    if claim_outcome == PROVED and verdict["outcome"] != PROVED:
        return (
            "You reported a proof, but no recorded compilation of this "
            "statement was accepted. "
            + verdict["reason"]
            + " Report the real outcome, or call `try_proof` with a proof that "
            "compiles."
        )
    if claim_outcome in (VERIFIED_TRUE, VERIFIED_FALSE):
        return (
            "A symbolic computation cannot establish a proof. Report the "
            "computation as a computation, or prove the claim in Lean."
        )
    return ""
