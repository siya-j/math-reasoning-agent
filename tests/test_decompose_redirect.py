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
