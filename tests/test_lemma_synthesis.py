"""Decomposition that ACTS. The controller attempts the holes it is given.

THE FAILURE THIS EXISTS FOR
---------------------------
`try_lemma` was registered, reachable, tested and — across the entire 4-goal
ProofNet run — called zero times. `exercise_1_26` produced four skeletons and
proved not one of their holes. The tool was never broken; nothing in the
control flow ever turned a hole into an attempt, and a prompt asking more
loudly is not a mechanism.

WHAT THE CONTROLLER DOES AND DOES NOT DECIDE
--------------------------------------------
The MODEL writes the decomposition. Lean typechecks it. Only then does the
controller attempt each hole, with the deterministic tactic ladder, spending
one compile per hole and no model call. It never invents a claim, never
chooses a mathematical strategy, and never accepts anything Lean did not.

`worth_proving` is the whole of the filtering, and it declines only claims
whose answer is already known: no claim, trivially true, circular, or already
handled.
"""

import asyncio

import pytest

from math_v2.core import log, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = "theorem mra_goal (Ω : Set ℂ) (h : IsOpen Ω) : f a = f b"
SKELETON = (
    "by\n"
    "  have h_deriv : ∀ x ∈ Ω, deriv f x = 0 := by sorry\n"
    "  have h_const : ∀ x ∈ Ω, f x = c := by sorry\n"
    "  exact absurd h_const h_deriv"
)
ONE_HOLE = "by\n  have h_deriv : ∀ x ∈ Ω, deriv f x = 0 := by sorry\n  exact foo h_deriv"

TYPECHECKS = "declaration uses 'sorry'"


def run(coro):
    return asyncio.run(coro)


def compiler(plan):
    """A Lean whose answer depends on what it is asked.

    `plan` maps a substring of the source to a LeanOutcome. First match wins;
    anything unmatched is rejected. This is how a test says "the first hole is
    provable and the second is not" without a compiler.
    """
    seen = []

    async def run_lean(source):
        seen.append(source)
        for needle, outcome in plan:
            if needle in source:
                output = TYPECHECKS if outcome is LeanOutcome.INCOMPLETE else ""
                return LeanResult(outcome, output)
        return LeanResult(LeanOutcome.ERRORS, "f.lean:1:1: error: unsolved goals")

    return run_lean, seen


@pytest.fixture
def workdir(tmp_path):
    log.clear(str(tmp_path))
    return str(tmp_path)


# ------------------------------------ A. one useful hole triggers synthesis
def test_a_typechecking_skeleton_attempts_its_hole(workdir):
    run_lean, seen = compiler([
        ("sorry", LeanOutcome.INCOMPLETE),        # the skeleton itself
        ("deriv f x = 0", LeanOutcome.COMPILED),  # the hole, as a lemma
    ])

    result = run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))

    assert result["outputs"]["lemmas_proved"] == ["mra_lemma_1"]
    assert log.records(workdir, log.LEMMA), "no lemma attempt was recorded"


def test_nothing_is_attempted_when_the_skeleton_does_not_typecheck(workdir):
    """Holes of a decomposition that does not combine are subgoals of nothing."""
    run_lean, seen = compiler([])          # everything is rejected

    result = run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))

    assert result["outputs"]["typechecks"] is False
    assert len(seen) == 1, "holes were attempted against a broken decomposition"


# --------------------------------- B. several holes are handled independently
def test_each_hole_is_attempted_separately(workdir):
    """The second failing must not stop the first from being kept."""
    # `sorry` first: the skeleton source contains BOTH claims, so it has to be
    # matched as the skeleton before either hole pattern can see it.
    run_lean, _ = compiler([
        ("sorry", LeanOutcome.INCOMPLETE),         # the skeleton itself
        ("f x = c", LeanOutcome.ERRORS),           # second hole: not provable
        ("deriv f x = 0", LeanOutcome.COMPILED),   # first hole: provable
    ])

    result = run(proving.try_skeleton(workdir, GOAL, SKELETON, run_lean, 4))

    assert result["outputs"]["lemmas_proved"] == ["mra_lemma_1"]
    assert any("f x = c" in c for c in result["outputs"]["outstanding"])
    assert result["outputs"]["accepted"] is False


# ------------------------------- C. an accepted lemma reaches the main proof
def test_a_kept_lemma_is_compiled_into_every_later_attempt(workdir):
    run_lean, seen = compiler([
        ("sorry", LeanOutcome.INCOMPLETE),
        ("deriv f x = 0", LeanOutcome.COMPILED),
    ])
    run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 1))

    later, later_seen = compiler([])
    run(proving.try_proof(workdir, GOAL, "by\n  have x : 1 = 1 := rfl\n  exact g x",
                          later))

    assert "mra_lemma_1" in later_seen[0], "the kept lemma was not in the file"


def test_the_lemma_is_kept_with_its_own_name_not_renamed(workdir):
    """`rename_goal` renames the LAST declaration. A kept lemma is prepended,
    so it must survive with the name the main proof will cite."""
    run_lean, _ = compiler([
        ("sorry", LeanOutcome.INCOMPLETE),
        ("deriv f x = 0", LeanOutcome.COMPILED),
    ])
    run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 1))

    assert any("mra_lemma_1" in kept for kept in log.kept_lemmas(workdir))


# ------------------------------------ D/E. nothing unproved is ever kept
def test_a_rejected_lemma_is_not_kept(workdir):
    run_lean, _ = compiler([("sorry", LeanOutcome.INCOMPLETE)])

    result = run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))

    assert result["outputs"]["lemmas_proved"] == []
    assert log.kept_lemmas(workdir) == []
    assert [r["status"] for r in log.records(workdir, log.LEMMA)] == [log.FALSE]


@pytest.mark.parametrize("outcome", [LeanOutcome.INCOMPLETE, LeanOutcome.CHEATED,
                                     LeanOutcome.TIMEOUT, LeanOutcome.UNAVAILABLE])
def test_no_cheating_route_can_make_a_lemma_count_as_proved(workdir, outcome):
    """The synthesised lemma goes through the same `interpret` as everything
    else. INCOMPLETE is `sorry`; CHEATED is `axiom` or `exact?`. Both COMPILE
    and neither proves anything."""
    run_lean, _ = compiler([
        ("deriv f x = 0", outcome),
        ("sorry", LeanOutcome.INCOMPLETE),
    ])

    run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))

    assert log.kept_lemmas(workdir) == []


# --------------------------------- F. the main proof is rebuilt and recompiled
def test_all_holes_closed_assembles_and_recompiles_the_goal(workdir):
    run_lean, seen = compiler([
        ("exact mra_lemma", LeanOutcome.COMPILED),   # the assembled proof
        ("deriv f x = 0", LeanOutcome.COMPILED),
        ("sorry", LeanOutcome.INCOMPLETE),
    ])

    result = run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))

    assert result["outputs"]["accepted"] is True
    assert "PROVED" in result["message"]
    assembled = [s for s in seen if "exact mra_lemma_1" in s]
    assert assembled, "the skeleton was never rebuilt with its lemma cited"
    assert "sorry" not in assembled[-1], "the assembled proof still had a hole"


def test_the_assembled_proof_is_recorded_as_a_proof_of_the_goal(workdir):
    """So `finish` can find it. A lemma record would not satisfy the guard."""
    run_lean, _ = compiler([
        ("exact mra_lemma", LeanOutcome.COMPILED),
        ("deriv f x = 0", LeanOutcome.COMPILED),
        ("sorry", LeanOutcome.INCOMPLETE),
    ])
    run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))

    assert log.accepted_proof(workdir, GOAL), "the goal was proved and not recorded"


def test_nothing_is_assembled_while_a_hole_is_open(workdir):
    run_lean, seen = compiler([
        ("deriv f x = 0", LeanOutcome.COMPILED),
        ("sorry", LeanOutcome.INCOMPLETE),
    ])

    run(proving.try_skeleton(workdir, GOAL, SKELETON, run_lean, 4))

    assert not any("exact mra_lemma_2" in s for s in seen)


# ------------------------------- G. useless, circular and duplicate claims
def test_a_claim_that_restates_the_goal_is_refused(workdir):
    """Proving the goal by assuming it. The claim is the goal's conclusion."""
    circular = "by\n  have h : f a = f b := by sorry\n  exact h"

    assert not proving.worth_proving("f a = f b", GOAL, workdir)

    run_lean, seen = compiler([("sorry", LeanOutcome.INCOMPLETE)])
    run(proving.try_skeleton(workdir, GOAL, circular, run_lean, 4))

    assert len(seen) == 1, "a compile was spent proving the goal from itself"


@pytest.mark.parametrize("claim", ["True", "( True )", "", "trivial"])
def test_trivial_and_unnamed_holes_are_skipped(workdir, claim):
    assert not proving.worth_proving(claim, GOAL, workdir)


def test_a_real_claim_is_not_skipped(workdir):
    assert proving.worth_proving("∀ x ∈ Ω, deriv f x = 0", GOAL, workdir)


def test_the_same_claim_is_not_attempted_twice(workdir):
    run_lean, seen = compiler([("sorry", LeanOutcome.INCOMPLETE)])

    run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))
    before = len(seen)
    run(proving.try_skeleton(workdir, GOAL,
                             ONE_HOLE.replace("exact foo", "exact bar"), run_lean, 4))

    assert len(seen) == before + 1, "a rejected claim was compiled again"


# ---------------------------------------------- H. the budget is respected
def test_the_fill_budget_bounds_the_compiles(workdir):
    many = "by\n" + "".join(
        f"  have h{i} : {i} < {i + 1} := by sorry\n" for i in range(8)
    ) + "  trivial"
    run_lean, seen = compiler([("sorry", LeanOutcome.INCOMPLETE)])

    run(proving.try_skeleton(workdir, GOAL, many, run_lean, 2))

    assert len(seen) == 1 + 2, f"budget 2 allowed {len(seen) - 1} fills"


def test_a_zero_budget_attempts_nothing(workdir):
    """What the tool layer passes when the compile budget is nearly spent."""
    run_lean, seen = compiler([("sorry", LeanOutcome.INCOMPLETE)])

    result = run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 0))

    assert len(seen) == 1
    assert result["outputs"]["lemmas_proved"] == []


def test_the_kept_lemma_cap_still_holds(workdir):
    for i in range(proving.MAX_KEPT_LEMMAS):
        log.keep_lemma(workdir, f"theorem old_{i} : True := trivial")
    run_lean, seen = compiler([
        ("sorry", LeanOutcome.INCOMPLETE),
        ("deriv", LeanOutcome.COMPILED),
    ])

    run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))

    assert len(log.kept_lemmas(workdir)) == proving.MAX_KEPT_LEMMAS


def test_the_compiles_used_are_reported_so_the_tool_layer_can_charge_them(workdir):
    run_lean, _ = compiler([
        ("deriv f x = 0", LeanOutcome.COMPILED),
        ("sorry", LeanOutcome.INCOMPLETE),
    ])

    result = run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 4))

    assert result["outputs"]["compiles_used"] >= 1


# ------------------------- I/J/K. nothing already verified may regress
def test_direct_proving_is_untouched(workdir):
    run_lean, seen = compiler([("mra_goal", LeanOutcome.COMPILED)])

    result = run(proving.try_proof(workdir, GOAL, "by\n  have h : 1 = 1 := rfl\n"
                                                  "  exact g h", run_lean))

    assert result["outputs"]["accepted"] is True
    assert len(seen) == 1


def test_the_generic_guard_still_fires(workdir):
    run_lean, seen = compiler([])
    run(proving.try_proof(workdir, GOAL, "by simp", run_lean))

    second = run(proving.try_proof(workdir, GOAL, "by aesop", run_lean))

    assert second["error"] == "generic_exhausted"


def test_refutation_is_untouched(workdir):
    run_lean, _ = compiler([("¬", LeanOutcome.COMPILED)])
    log.set_goal(workdir, GOAL)

    result = run(proving.try_refutation(workdir, "", "by intro h; exact g h", run_lean))

    assert result["outputs"]["refuted"] is True


def test_a_placeholder_skeleton_hole_is_still_allowed(workdir):
    """`sorry` is the POINT of a skeleton and must not hit the proof guard."""
    run_lean, seen = compiler([("sorry", LeanOutcome.INCOMPLETE)])

    result = run(proving.try_skeleton(workdir, GOAL, ONE_HOLE, run_lean, 0))

    assert result["outputs"]["typechecks"] is True
