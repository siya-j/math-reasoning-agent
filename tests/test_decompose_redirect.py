"""Past three rejected attempts, the next move must be a smaller claim.

MEASURED on eval/results/mixed-1-rebuilt.json, 32 decided goals.

Of the 24 proofs, TWELVE landed on the first attempt at the goal and TWENTY
within three -- 50% and 83%. The direct route either works almost immediately
or does not work at all.

The failures went well past that and kept going straight. Unproved goals spent
MORE than proved ones on every axis: median 2.0 goal attempts against 1.5, 7.5
compiles against 3.0, 9.0 searches against 3.5. They also searched 1.39 times
per compile where proofs searched 0.99. So when the direct route stopped
paying, the agent did more of it, and more searching.

What it did not do is decompose. Only 6 of 32 goals kept a single helper
lemma, and decomposition CLOSED 3 of those -- two of the `hard` tier's only
successes, and `exercise_4_6`, one of only two ProofNet proofs in the whole
run (9 attempts, 4 lemmas, 20 compiles). A mode reached six times and paying
three times is not a mode to leave to the model's discretion.

Every test drives the real `try_proof` with a real record. A fixture asserting
on a hand-built log would pass against a guard that had been removed.
"""

import asyncio

import pytest

from math_v2.core import log, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = "theorem mra_goal (n : Nat) : n + 0 = n"


def run(coro):
    return asyncio.run(coro)


async def rejects(source):
    return LeanResult(LeanOutcome.ERRORS, "3:2: error: unsolved goals\nn : Nat\n⊢ n + 0 = n")


async def accepts(source):
    return LeanResult(LeanOutcome.COMPILED, "")


@pytest.fixture
def workdir(tmp_path):
    log.clear(str(tmp_path))
    log.set_goal(str(tmp_path), GOAL)
    return str(tmp_path)


def _attempt(workdir, proof, runner=rejects, **kw):
    """One real attempt at the goal, through the real guard chain."""
    return run(proving.try_proof(workdir, GOAL, proof, runner, **kw))


# Non-generic on purpose. `_generic_already_failed` refuses a second bare
# closer WITHOUT compiling, so a test built on `by simp` after `by norm_num`
# would never reach the guard under test and would pass with it deleted.
DRAFTS = [
    "by exact Nat.add_zero n",
    "by exact (Nat.add_zero n).symm ▸ rfl",
    "by simpa [Nat.add_zero] using rfl",
    "by exact congrArg (· + 0) rfl",
    "by exact Nat.zero_add n ▸ rfl",
]


# ------------------------------------------------------------ THE regression
def test_a_fourth_direct_attempt_is_redirected(workdir):
    """Three rejections buy a redirect, not a fourth attempt of the same kind."""
    for draft in DRAFTS[:3]:
        result = _attempt(workdir, draft)
        assert result.get("error") != "decompose_first", (
            "redirected before the threshold was reached"
        )

    result = _attempt(workdir, DRAFTS[3])

    assert result["error"] == "decompose_first", result.get("message", "")
    assert "try_lemma" in result["message"]
    assert len(log.records(workdir, log.PROOF)) == 3, (
        "the refusal was recorded as an attempt; a refusal is not an attempt"
    )


def test_three_attempts_are_not_redirected(workdir):
    """The line on the other side. `exercise_1_5` is a real ProofNet proof
    that landed on its THIRD attempt, so a threshold of two would have refused
    a goal this system actually proved."""
    for draft in DRAFTS[:2]:
        _attempt(workdir, draft)

    result = _attempt(workdir, DRAFTS[2])

    assert result.get("error") != "decompose_first"
    assert len(log.records(workdir, log.PROOF)) == 3, "the third attempt never compiled"


def test_a_lemma_attempt_lifts_it_whether_or_not_it_worked(workdir):
    """Gated on what the record shows, never on merit -- the same rule
    `_skeleton_loop_refusal` uses. A REJECTED `try_lemma` is still the model
    engaging with a smaller claim."""
    for draft in DRAFTS[:3]:
        _attempt(workdir, draft)
    assert _attempt(workdir, DRAFTS[3])["error"] == "decompose_first"

    run(proving.try_lemma(workdir, "lemma helper (n : Nat) : n = n",
                          "by exact absurd rfl (by simp)", rejects))

    result = _attempt(workdir, DRAFTS[3])
    assert result.get("error") != "decompose_first", (
        "a real lemma attempt did not lift the redirect"
    )


def test_it_re_arms_after_the_next_failure(workdir):
    """Not a one-time nudge. Three failures buy one lemma attempt, that buys
    one direct attempt, and a further failure asks for another lemma -- the
    alternation is the intent."""
    for draft in DRAFTS[:3]:
        _attempt(workdir, draft)
    run(proving.try_lemma(workdir, "lemma helper (n : Nat) : n = n", "by rfl", accepts))

    # Lifted, so this one compiles -- and is rejected, which re-arms the guard.
    assert _attempt(workdir, DRAFTS[3]).get("error") != "decompose_first"

    result = _attempt(workdir, DRAFTS[4])
    assert result["error"] == "decompose_first", (
        "the redirect did not re-arm after a further failed direct attempt"
    )


def test_automatic_hole_filling_cannot_lift_it(workdir):
    """`log.Record.auto` exists for exactly this. Automatic filling writes
    LEMMA records too, and counting them would let the system satisfy the
    condition on the model's behalf."""
    for draft in DRAFTS[:3]:
        _attempt(workdir, draft)

    log.append(workdir, log.Record(
        kind=log.LEMMA, statement="lemma auto_filled : 1 = 1", proof="by rfl",
        status=log.TRUE, detail="filled automatically", auto=True))

    result = _attempt(workdir, DRAFTS[3])
    assert result["error"] == "decompose_first", (
        "an automatically filled hole lifted a guard meant to require the "
        "model's own lemma work"
    )


def test_attempts_at_other_statements_do_not_count(workdir):
    """Counted per goal, from the record. A helper or a diversion is not an
    attempt at this goal, and `exercise_1_19` shows why that distinction
    matters: it had seven compiles and zero attempts at its goal."""
    for i in range(4):
        run(proving.try_proof(workdir, f"theorem other_{i} : {i} = {i}",
                              f"by exact Nat.le_refl {i}", rejects))

    result = _attempt(workdir, DRAFTS[0])
    assert result.get("error") != "decompose_first", (
        "attempts at unrelated statements were counted against this goal"
    )


def test_the_repair_path_is_not_redirected(workdir):
    """The automatic repair at the bottom of `try_proof` recurses with
    `repair=False`. That call is not the model choosing to try again, and the
    rejection that triggered it is already logged -- so without the exemption
    the repair would be refused by the count it had just created."""
    for draft in DRAFTS[:3]:
        _attempt(workdir, draft)

    result = _attempt(workdir, DRAFTS[3], repair=False)

    assert result.get("error") != "decompose_first"
    assert len(log.records(workdir, log.PROOF)) == 4, (
        "the repair path never reached the compiler"
    )


def test_an_accepted_attempt_is_not_counted_against_the_goal(workdir):
    """Only REJECTED attempts count. A goal that has already been proved has
    nothing to redirect."""
    for draft in DRAFTS[:3]:
        _attempt(workdir, draft, runner=accepts)

    result = _attempt(workdir, DRAFTS[3])
    assert result.get("error") != "decompose_first"


# =====================================================================
# RECURSION: a lemma that will not go through is itself a target
# =====================================================================
# Mirrors HILBERT (arXiv 2509.22819), whose PutnamBench result -- 70% with
# Gemini 2.5 Pro, roughly 20 points above prior work -- comes from this step:
# when a subgoal fails both the prover and the retrieval-augmented reasoner,
# it decomposes THAT SUBGOAL rather than returning to the top.
#
# There is no stack and no depth counter. `_attempts_exhausted` takes the
# record kind, so `log.PROOF` asks for a lemma and `log.LEMMA` asks for
# something smaller than that lemma, one level down and the level below that.

LEMMA = "lemma helper (n : Nat) : n + 0 = n"
LEMMA_DRAFTS = [
    "by exact Nat.add_zero n",
    "by exact (Nat.add_zero n).symm ▸ rfl",
    "by simpa [Nat.add_zero] using rfl",
    "by exact congrArg (· + 0) rfl",
]


def _lemma(workdir, statement, proof, runner=rejects):
    return run(proving.try_lemma(workdir, statement, proof, runner))


def test_a_stuck_lemma_is_itself_redirected(workdir):
    """THE recursive step. Three rejected attempts at a helper, and the
    fourth is refused in favour of something smaller than the helper."""
    for draft in LEMMA_DRAFTS[:3]:
        result = _lemma(workdir, LEMMA, draft)
        assert result.get("error") != "decompose_first"

    result = _lemma(workdir, LEMMA, LEMMA_DRAFTS[3])

    assert result["error"] == "decompose_first", result.get("message", "")
    assert "THIS LEMMA" in result["message"], result["message"]
    assert "crux" in result["message"]


def test_a_retry_of_the_stuck_lemma_cannot_lift_its_own_refusal(workdir):
    """Guaranteed by arithmetic, not by a special case -- which is worth
    pinning because an earlier draft of this guard carried an explicit "on a
    different claim" condition for it, and two successive mutation runs showed
    no test could distinguish that condition's presence from its absence.

    The reason it is unnecessary: a retry of the stuck lemma is itself a
    rejected record for the same target, so it increments the count and moves
    the threshold past itself. Nothing appears "after the last rejection"
    because the retry becomes the last rejection.
    """
    for draft in LEMMA_DRAFTS[:3]:
        _lemma(workdir, LEMMA, draft)
    assert proving._attempts_exhausted(workdir, LEMMA, log.LEMMA) is not None

    log.append(workdir, log.Record(
        kind=log.LEMMA, statement=LEMMA, proof="by another_try",
        status=log.UNKNOWN, detail="rejected again"))

    assert proving._attempts_exhausted(workdir, LEMMA, log.LEMMA) is not None, (
        "a retry of the stuck lemma lifted its own refusal"
    )


def test_a_smaller_lemma_lifts_it(workdir):
    """Complying works: a different, smaller claim re-opens the stuck one."""
    for draft in LEMMA_DRAFTS[:3]:
        _lemma(workdir, LEMMA, draft)
    assert _lemma(workdir, LEMMA, LEMMA_DRAFTS[3])["error"] == "decompose_first"

    _lemma(workdir, "lemma smaller : (0 : Nat) + 0 = 0", "by rfl", accepts)

    result = _lemma(workdir, LEMMA, LEMMA_DRAFTS[3])
    assert result.get("error") != "decompose_first", (
        "a genuinely smaller lemma did not re-open the stuck one"
    )


def test_the_goal_and_a_lemma_are_tracked_separately(workdir):
    """Failures are counted per target. Three failed lemma attempts must not
    redirect the goal, and vice versa -- otherwise recursion would collapse
    into one shared counter."""
    for draft in LEMMA_DRAFTS[:3]:
        _lemma(workdir, LEMMA, draft)

    result = _attempt(workdir, DRAFTS[0])
    assert result.get("error") != "decompose_first", (
        "lemma failures were counted against the goal"
    )


# ---------------------------------------------------- the deadlock
def test_the_goal_is_not_refused_when_no_lemma_can_be_kept(workdir):
    """MEASURED as a live deadlock in the first version of this guard.

    With the kept-lemma cap spent, `try_lemma` short-circuits WITHOUT writing
    a record, so the lift condition can never be met: `try_proof` answered
    `decompose_first` and `try_lemma` answered "lemma budget spent" until the
    tool-call ceiling killed the goal. It would have fired on exactly the
    goals that decompose most -- the four in the mixed run that reached the
    old cap of four.

    A guard must never refuse what the agent cannot comply with.
    """
    for index in range(proving.MAX_KEPT_LEMMAS):
        _lemma(workdir, f"lemma filler_{index} : ({index} : Nat) = {index}",
               "by rfl", accepts)
    assert len(log.kept_lemmas(workdir)) == proving.MAX_KEPT_LEMMAS

    for draft in DRAFTS[:3]:
        _attempt(workdir, draft)

    result = _attempt(workdir, DRAFTS[3])
    assert result.get("error") != "decompose_first", (
        "refused to let the agent attempt the goal while giving it no way "
        "to comply"
    )


def test_the_lemma_cap_refusal_says_it_did_not_compile(workdir):
    """It returns an `error` now, which is what gets the charged compilation
    refunded -- it used to report `ok: True` and bill one for a message."""
    for index in range(proving.MAX_KEPT_LEMMAS):
        _lemma(workdir, f"lemma filler_{index} : ({index} : Nat) = {index}",
               "by rfl", accepts)

    result = _lemma(workdir, "lemma one_more : (1 : Nat) = 1", "by rfl", accepts)
    assert result["error"] == "lemma_budget_spent"
    assert "not compiled" in result["message"]


def test_the_kept_lemma_cap_resolves_at_call_time(workdir):
    """`limit` was a default argument bound at definition time, so patching
    the module constant -- which is how a test varies it -- left `try_lemma`
    using the value captured at import."""
    import math_v2.core.proving as module

    original = module.MAX_KEPT_LEMMAS
    try:
        module.MAX_KEPT_LEMMAS = 1
        _lemma(workdir, "lemma a : (1 : Nat) = 1", "by rfl", accepts)
        result = _lemma(workdir, "lemma b : (2 : Nat) = 2", "by rfl", accepts)
        assert result.get("error") == "lemma_budget_spent", (
            "the patched cap was ignored, so the default bound at import"
        )
    finally:
        module.MAX_KEPT_LEMMAS = original
