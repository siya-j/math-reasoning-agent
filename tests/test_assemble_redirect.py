"""Enough helpers, none tried against the goal: assemble before decomposing more.

MEASURED on eval/results/headroom-flash35.json, `exercise_1_19`:

    goal attempts     1
    lemma attempts   17     kept 7
    skeletons         4
    compiles         25/40   -> 88% on helpers, ONE on the goal
    via_synthesis  False

It decomposed enthusiastically and never assembled. THE CAUSE WAS OURS: the
decomposition redirect shipped without its counterpart, and MAX_KEPT_LEMMAS
was raised 4 -> 8 the same day. At four, the `lemma_budget_spent` refusal
fired at the cap and said "Use the ones you have and prove the goal" -- that
WAS the pushback, and widening the cap removed it. The same goal one commit
earlier hit 4/4 kept and made FOUR goal attempts.

So the cap was doing two jobs badly: bounding how many lemmas may be kept, and
deciding when assembling must be tried. Those are now separate.
"""

import asyncio

import pytest

from math_v2.core import log, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = "theorem mra_goal (n : Nat) : n + 0 = n"


def run(coro):
    return asyncio.run(coro)


async def accepts(source):
    return LeanResult(LeanOutcome.COMPILED, "")


async def rejects(source):
    return LeanResult(LeanOutcome.ERRORS, "3:2: error: unsolved goals")


@pytest.fixture
def workdir(tmp_path):
    d = str(tmp_path)
    log.clear(d)
    run(proving.check_statement(d, GOAL, accepts))
    return d


def _keep(workdir, index):
    """One genuinely kept lemma, through the real `try_lemma`."""
    return run(proving.try_lemma(
        workdir, f"lemma helper_{index} : ({index} : Nat) = {index}",
        "by rfl", accepts))


def _attempt(workdir, proof="by exact Nat.add_zero n", runner=rejects):
    return run(proving.try_proof(workdir, GOAL, proof, runner))


# ------------------------------------------------------- THE regression
def test_a_fourth_lemma_is_refused_until_the_goal_is_tried(workdir):
    for index in range(proving.ASSEMBLE_AFTER):
        assert _keep(workdir, index).get("error") is None

    result = _keep(workdir, 99)

    assert result["error"] == "assemble_first", result.get("message", "")
    assert "try_proof" in result["message"]
    # It names what is available, or the instruction is not actionable.
    assert "helper_0" in result["message"], result["message"]


def test_below_the_threshold_nothing_is_refused(workdir):
    for index in range(proving.ASSEMBLE_AFTER - 1):
        assert _keep(workdir, index).get("error") is None
    assert log.kept_lemmas(workdir)


def test_one_goal_attempt_lifts_it(workdir):
    for index in range(proving.ASSEMBLE_AFTER):
        _keep(workdir, index)
    assert _keep(workdir, 99)["error"] == "assemble_first"

    _attempt(workdir)  # rejected, and that is enough

    assert _keep(workdir, 98).get("error") is None, (
        "a real attempt at the goal did not lift the refusal"
    )


def test_it_re_arms_after_more_lemmas(workdir):
    """Alternation, not a one-time nudge."""
    for index in range(proving.ASSEMBLE_AFTER):
        _keep(workdir, index)
    _attempt(workdir)
    assert _keep(workdir, 50).get("error") is None

    result = _keep(workdir, 51)
    assert result["error"] == "assemble_first", (
        "it did not re-arm after further lemmas with no goal attempt"
    )


def test_rejected_lemmas_do_not_count(workdir):
    """Counted on KEPT lemmas. A rejected lemma is not an asset the goal could
    have cited, so it is not evidence of over-decomposition -- and
    `exercise_1_19` made 17 attempts for 7 kept."""
    for index in range(6):
        run(proving.try_lemma(workdir, f"lemma bad_{index} : ({index}:Nat) = {index}",
                              "by rfl", rejects))
    assert not log.kept_lemmas(workdir)

    assert _keep(workdir, 0).get("error") is None


def test_nothing_declared_means_no_refusal(tmp_path):
    """Same exemption as the drift guard: with no statement check on record
    there is no goal to assemble against, and a run configured not to check
    statements must not be blocked from proving lemmas."""
    d = str(tmp_path)
    log.clear(d)
    for index in range(proving.ASSEMBLE_AFTER + 2):
        assert _keep(d, index).get("error") is None


# --------------------------------------------------- THE deadlock property
def test_the_two_redirects_can_never_deadlock(workdir):
    """THE test that makes this safe to ship.

    `_attempts_exhausted` refuses a goal attempt and demands a lemma.
    `_lemmas_without_assembly` refuses a lemma and demands a goal attempt. If
    both could arm at once the agent would have no legal move and would spin
    until the tool-call ceiling killed the goal -- which is exactly the
    deadlock that the kept-lemma cap caused in 1f1ce40.

    They cannot, by construction: the first needs no lemma work since the last
    goal failure, the second needs no goal attempt since the last kept lemma,
    and whichever happened last lifts the other. This walks the state through
    both directions and asserts a legal move always exists.
    """
    for index in range(proving.ASSEMBLE_AFTER):
        _keep(workdir, index)

    # Armed: lemma refused. The goal must therefore be allowed.
    assert _keep(workdir, 90)["error"] == "assemble_first"
    assert proving._attempts_exhausted(workdir, GOAL, log.PROOF) is None, (
        "both guards armed at once: no legal move"
    )

    # Now fail the goal repeatedly to arm the other direction.
    for proof in ("by exact Nat.add_zero n",
                  "by exact (Nat.add_zero n).symm ▸ rfl",
                  "by simpa [Nat.add_zero] using rfl"):
        _attempt(workdir, proof)

    # Armed the other way: a lemma must therefore be allowed.
    assert proving._attempts_exhausted(workdir, GOAL, log.PROOF) is not None
    assert proving._lemmas_without_assembly(workdir) is None, (
        "both guards armed at once: no legal move"
    )


def test_a_lemma_is_still_allowed_when_the_goal_is_refused(workdir):
    """The same property end to end, through the tools rather than the
    helpers: with the goal redirect armed, `try_lemma` must go through."""
    for proof in ("by exact Nat.add_zero n",
                  "by exact (Nat.add_zero n).symm ▸ rfl",
                  "by simpa [Nat.add_zero] using rfl"):
        _attempt(workdir, proof)
    assert _attempt(workdir, "by exact congrArg (· + 0) rfl")["error"] == \
        "decompose_first"

    assert _keep(workdir, 0).get("error") is None, (
        "the goal was refused and so was the lemma; the agent had no move"
    )
