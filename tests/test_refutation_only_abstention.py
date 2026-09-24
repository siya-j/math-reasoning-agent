"""An abstention is not an inconclusive result.

Found offline, before any budget was spent, by asking whether the scorer
would turn verifier verdicts into the right buckets.

The system prompt tells the agent to sanity-check a physical quantity it has
computed. The plausibility verifier answers such a check with UNKNOWN when
it finds nothing wrong — it is structurally incapable of returning TRUE. The
guard counted that UNKNOWN among the checks that failed to decide, so:

    check_numeric   -> TRUE
    check_possible  -> UNKNOWN
    guard           -> UNKNOWN

A correct, fully verified answer was downgraded to unverified for the sole
reason that the agent had sanity-checked it. The instruction to be careful
was penalised by the thing measuring care.

The fix is a distinction, not a special case: a verifier declares whether it
can ever confirm, and the guard sets aside the abstentions of those that
cannot. A refutation from the same verifier still counts, and counts
decisively.
"""

import verifiers
from domain.check import Check
from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from pipeline import guard
from pipeline.tools import VerificationLog, make_tools

QUESTION = "An engine takes in 100 J and does 40 J of work. Is its efficiency 0.4?"


def check(tool: str, method: str, status: VerificationStatus) -> Check:
    return Check(
        tool=tool,
        claim="a claim",
        request=VerificationRequest(kind=VerificationKind.NUMERIC, lhs="1", rhs="1"),
        verdict=Verdict(status=status, method=method, detail="because"),
    )


# ------------------------------------------------------------- the registry
def test_the_guard_does_not_hardcode_a_verifier_name():
    """The set is derived from the verifiers themselves, so a new
    refutation-only verifier is honoured without editing the guard."""
    assert "plausibility" in verifiers.REFUTATION_ONLY
    assert "sympy" not in verifiers.REFUTATION_ONLY
    assert verifiers.REFUTATION_ONLY == frozenset(
        v.name for v in verifiers.VERIFIERS if v.refutes_only
    )


# ---------------------------------------------------------------- the fix
def test_a_sanity_check_does_not_downgrade_a_verified_answer():
    verdict = guard.decide(
        QUESTION,
        [
            check("check_numeric", "sympy", VerificationStatus.TRUE),
            check("check_possible", "plausibility", VerificationStatus.UNKNOWN),
        ],
    )
    assert verdict.status is VerificationStatus.TRUE


def test_the_whole_path_agrees_end_to_end():
    """Not a hand-built Check list: the real tools, the real verifiers."""
    log = VerificationLog()
    by_name = {tool.__name__: tool for tool in make_tools(log)}
    by_name["check_numeric"]("efficiency is 0.4", "40/100", "0.4")
    by_name["check_possible"]("efficiency is 0.4", "efficiency", "0.4")

    assert guard.decide(QUESTION, log.checks).status is VerificationStatus.TRUE


# -------------------------------------------------- what must NOT be weakened
def test_a_refutation_still_counts_and_still_wins():
    """Only the abstention is set aside. Impossibility is decisive."""
    verdict = guard.decide(
        QUESTION,
        [
            check("check_numeric", "sympy", VerificationStatus.TRUE),
            check("check_possible", "plausibility", VerificationStatus.FALSE),
        ],
    )
    assert verdict.status is VerificationStatus.FALSE


def test_an_ordinary_inconclusive_check_still_downgrades():
    """A SymPy UNKNOWN means it could not decide, which is a real gap in the
    evidence and must still prevent a verified verdict."""
    verdict = guard.decide(
        QUESTION,
        [
            check("check_numeric", "sympy", VerificationStatus.TRUE),
            check("check_equality", "sympy", VerificationStatus.UNKNOWN),
        ],
    )
    assert verdict.status is VerificationStatus.UNKNOWN


def test_sanity_checks_alone_verify_nothing():
    """The agent checked only that nothing was impossible. Nothing was
    confirmed, because no check made was capable of confirming."""
    verdict = guard.decide(
        QUESTION,
        [check("check_possible", "plausibility", VerificationStatus.UNKNOWN)],
    )
    assert verdict.status is VerificationStatus.NOT_APPLICABLE
    assert "only refute" in verdict.detail


def test_no_checks_at_all_is_still_the_strongest_refusal():
    verdict = guard.decide(QUESTION, [])
    assert verdict.status is VerificationStatus.NOT_APPLICABLE
    assert "no deterministic verification" in verdict.detail.lower()


def test_the_lint_still_runs_over_every_check():
    """Setting an abstention aside must not smuggle it past the lint."""
    unfaithful = Check(
        tool="check_quantity",
        claim="a claim",
        request=VerificationRequest(
            kind=VerificationKind.QUANTITY, lhs="x", rhs="99999*meter"
        ),
        verdict=Verdict(VerificationStatus.TRUE, "units", "because"),
    )
    verdict = guard.decide(
        "Is the distance 5 metres?",
        [unfaithful, check("check_possible", "plausibility",
                           VerificationStatus.UNKNOWN)],
    )
    assert verdict.status is VerificationStatus.UNKNOWN
    assert verdict.method == "faithfulness lint"
