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

from pipeline.faithfulness import unsupported_in

from math_v2.core import log

# What `finish` is allowed to conclude.
PROVED = "proved"
VERIFIED_TRUE = "verified_true"
VERIFIED_FALSE = "verified_false"
NOT_PROVED = "not_proved"
NOT_FORMALIZED = "not_formalized"
NOT_VERIFIED = "not_verified"
# A REPORT that the statement looks false or ill-posed. Not a verdict — we
# cannot confirm it — but it ends the run, so it is guarded like one.
STATEMENT_SUSPECT = "statement_suspect"
# The same report, once Lean has accepted a proof of the NEGATION. This one IS
# a verdict: it rests on a compilation, not on the agent's account of one. The
# agent never claims it — `finish` derives it from the record.
REFUTED = "refuted"

BANNERS = {
    PROVED: "PROVED (Lean accepted a complete proof)",
    VERIFIED_TRUE: "VERIFIED TRUE",
    VERIFIED_FALSE: "VERIFIED FALSE",
    NOT_PROVED: "NOT PROVED",
    NOT_FORMALIZED: "NOT FORMALISED",
    NOT_VERIFIED: "NOT VERIFIED",
    STATEMENT_SUSPECT: "STATEMENT SUSPECT (the agent's report, not a verdict)",
    REFUTED: "REFUTED (Lean accepted a proof of the negation)",
}


def _negation_of(statement: str) -> str:
    """Imported lazily: `core.proving` imports this module's siblings, and a
    top-level import would close the cycle."""
    from math_v2.core.proving import negation_of

    return negation_of(statement)


def attempted_a_refutation(workdir: str) -> bool:
    """Did the agent put a counterexample to the compiler? Pass or fail."""
    return any(
        (record.get("proof") or "").strip()
        for record in log.records(workdir, log.REFUTATION)
    )


def verified_refutation(workdir: str) -> dict:
    """The accepted proof of the goal's negation, or {}.

    Read from `kind == REFUTATION` records with `status == TRUE`, which are
    written by `core.proving.try_refutation` only after `interpret` reported
    the compiler accepted a COMPLETE proof. A file that compiled via `sorry`
    comes back INCOMPLETE and one that leaned on `axiom` comes back CHEATED;
    neither is TRUE, so neither reaches here.
    """
    for record in log.records(workdir, log.REFUTATION):
        if record.get("status") == log.TRUE:
            return record
    return {}


def attempted_a_proof(workdir: str) -> bool:
    """Did the agent actually put a proof to the compiler for this goal?

    A statement check does NOT count. It compiles the signature with a
    placeholder and answers "can Lean parse this", which is not an attempt at
    the mathematics.
    """
    return any(
        (record.get("proof") or "").strip()
        for record in log.records(workdir, log.PROOF)
    )


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


def faithfulness_failure(statement: str, claim: str) -> str:
    """Why a compiled proof still does not answer the question asked. "" if it does.

    THE GAP THIS CLOSES
    -------------------
    Lean proves that the STATEMENT is true. Nothing proves the statement says
    what the user asked. That gap produced failures 3 and 8 in this project:
    asked "is 2 the only solution of x^2 = 4?", the system checked "are the
    solutions 2 and -2?", every component behaved correctly, and the answer
    addressed a question nobody asked.

    A compiled proof of the wrong theorem is the same failure with a proof
    assistant attached, and it is MORE convincing, which makes it worse.

    Deliberately narrow, and arithmetic rather than another language model: it
    compares the numbers in the formal statement against the numbers in the
    question. It cannot see `sin` swapped for `cos`. It catches one specific,
    observed, damaging mistake.
    """
    if not claim.strip() or not statement.strip():
        return ""

    invented = unsupported_in(statement, claim)
    if not invented:
        return ""
    return (
        "The formal statement uses " + ", ".join(sorted(set(invented))) + ", which "
        "the question never mentions, so what was proved is not what was asked. "
        "Restate the theorem using only the values in the question, and prove "
        "that."
    )


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


def suspect_refusal(workdir: str) -> str:
    """Why `statement_suspect` is not yet allowed. "" when it is.

    MEASURED. On the four ProofNet goals the agent reported `statement_suspect`
    three times and put a proof to the compiler ZERO times, once, and zero
    times. `genuinely_tested` came out 0 of 4.

    The mathematics was right — Stein & Shakarchi Ch.1 Ex.13 is stated over a
    REGION, open and connected, and the ProofNet port has only `IsOpen`, so
    without connectedness f can be a different constant on each component. That
    is one of the 118/371 broken rows arXiv 2406.07222 documents, and finding
    them is worth having.

    But the exit cost nothing. Every other terminal claim in this system is
    checked against recorded tool executions; this one was checked against
    prose, and it ENDS THE RUN. So it had a gradient: quitting became cheaper
    than proving, and once searching was capped the freed budget went to
    quitting rather than to the compiler.

    The rule is therefore effort, not correctness — we cannot check the
    mathematics, and blocking the report outright would suppress a real finding
    on a benchmark that is 31.8% broken. One compiled, rejected proof attempt
    is enough: it makes the report cost the same as trying, so the agent has no
    reason to prefer it, and a rejection also produces the goal state that
    usually shows WHY the statement is wrong.
    """
    if not attempted_a_proof(workdir):
        return (
            "You reported the statement as suspect without ever putting a proof "
            "to the compiler. Failing to see a proof is not evidence that none "
            "exists, and this report ends the run. Call `try_proof` with your "
            "best attempt first — if the statement really is false the "
            "rejection will show you which hypothesis is missing, and you may "
            "then report it."
        )

    # SECOND GATE, ADDED AFTER THE 4-GOAL RUN. All three suspect reports came
    # with a counterexample written out in prose — Ω = D(-2,1) ∪ D(2,1), f = 0
    # on one disc and something else on the other — and `try_refutation` was
    # never called once. The tool existed and the prompt described it; nothing
    # in the CONTROL FLOW ever put the model in front of it, so the cheapest
    # path was still to describe the counterexample and stop.
    #
    # So the ask is made here, where the exit is, and made once. The negation
    # is constructed for the model rather than requested from it, because
    # restating the binders is the step it gets wrong and the step a regex can
    # do exactly. One refused attempt is enough to pass — the requirement is
    # that the counterexample was PUT to Lean, not that it succeeded.
    if not attempted_a_refutation(workdir):
        goal = log.current_goal(workdir)
        negation = _negation_of(goal)
        offer = (
            f"\n\nThe negation of your goal is:\n\n    {negation}\n\n"
            "Call `try_refutation` with that statement and a proof of it."
            if negation else
            "\n\nState the negation as a theorem and prove it with "
            "`try_refutation`."
        )
        return (
            "You have described a counterexample but never compiled one. In "
            "prose that establishes nothing; in Lean it establishes the whole "
            "claim, and the goal is then reported as REFUTED — a verified "
            "result rather than an unverified suspicion." + offer +
            "\n\nInstantiate it: give the concrete function, set and points "
            "from your counterexample, then derive the contradiction. If the "
            "attempt is rejected you may report `statement_suspect` anyway — "
            "what is not allowed is not having tried."
        )
    return ""
