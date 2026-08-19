"""A term where Lean wanted a tactic. One repair, `exact`, and nothing else.

MEASURED on eval/results/near-mathlib-repl.json. Three attempts across two
goals were rejected for a missing keyword and nothing else:

    top-compact-image       1. hs.image hf              -> unknown tactic
                            2. by exact hs.image hf     -> ACCEPTED

    grp-subgroup-of-cyclic  2. by inferInstance         -> unknown tactic
                            3. inferInstance            -> unknown tactic
                            4. by exact inferInstance   -> ACCEPTED

The mathematics was right in every one of them. `declaration()` wraps a proof
body as `by\\n  <body>`, so a bare TERM becomes a tactic name and Lean answers
"unknown tactic" — an error the classifier did not even recognise.

DELIBERATELY ONE FORM. `apply`, `refine`, `simpa`, `exact ⟨_, ·⟩` and the rest
have no evidence behind them in this corpus, and a repair ladder that rewrites
what the model submitted is a repair ladder that can corrupt a proof search.
"""

import asyncio

import pytest

from math_v2.core import diagnosis, log, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = ("theorem continuous_image_compact (hs : IsCompact s) (hf : Continuous f) "
        ": IsCompact (f '' s)")
UNKNOWN_TACTIC = "f.lean:2:3: error: unknown tactic\n\nStill to prove:\n⊢ IsCompact (f '' s)"


def run(coro):
    return asyncio.run(coro)


def compiler(accepts=None, error=UNKNOWN_TACTIC):
    """Accepts any source containing `accepts`; rejects everything else."""
    seen = []

    async def run_lean(source):
        seen.append(source)
        if accepts and accepts in source:
            return LeanResult(LeanOutcome.COMPILED, "")
        return LeanResult(LeanOutcome.ERRORS, error)

    return run_lean, seen


@pytest.fixture
def workdir(tmp_path):
    log.clear(str(tmp_path))
    return str(tmp_path)


# ------------------------------------------- the error is now classified
def test_unknown_tactic_is_classified():
    assert diagnosis.classify(UNKNOWN_TACTIC) is diagnosis.UNKNOWN_TACTIC


def test_it_is_not_confused_with_a_tactic_that_failed():
    """Opposite responses: one needs `exact`, the other needs a new strategy."""
    failed = "4:2: warning: aesop: failed to prove the goal after exhaustive search."

    assert diagnosis.classify(failed) is diagnosis.TACTIC_FAILED
    assert diagnosis.classify(UNKNOWN_TACTIC) is not diagnosis.TACTIC_FAILED


def test_the_instruction_names_the_fix():
    action = diagnosis.next_action(UNKNOWN_TACTIC)

    assert "exact" in action and "TERM" in action


# --------------------------------------- what does and does not get repaired
@pytest.mark.parametrize("proof,expected", [
    # The two recorded cases.
    ("hs.image hf", "by\n  exact hs.image hf"),
    ("inferInstance", "by\n  exact inferInstance"),
    # `by foo` and `foo` are the same mistake.
    ("by inferInstance", "by\n  exact inferInstance"),
])
def test_a_bare_term_is_wrapped(proof, expected):
    assert diagnosis.exact_repair(proof) == expected


@pytest.mark.parametrize("proof", [
    "aesop", "by aesop", "simp", "by rfl",              # real tactics
    "by exact hs.image hf",                             # already correct
    "rcases foo with ⟨x, hx⟩",                          # tactic-shaped
    "by\n  have h : X := y\n  exact h",                 # a tactic block
    "by sorry", "admit",                                # placeholders
    "",
])
def test_nothing_else_is_touched(proof):
    """A repair that can rewrite a legitimate tactic can corrupt a search."""
    assert diagnosis.exact_repair(proof) == ""


# ------------------------------------------ the repair on the proving path
def test_the_recorded_top_compact_image_case_now_succeeds(workdir):
    run_lean, seen = compiler(accepts="exact hs.image hf")

    result = run(proving.try_proof(workdir, GOAL, "hs.image hf", run_lean))

    assert result["outputs"]["accepted"] is True
    assert "exact hs.image hf" in seen[1]
    assert "automatic repair" in result["message"]


def test_the_recorded_grp_subgroup_case_now_succeeds(workdir):
    goal = "theorem subgroup_cyclic (H : Subgroup G) : IsCyclic H"
    run_lean, seen = compiler(accepts="exact inferInstance")

    result = run(proving.try_proof(workdir, goal, "inferInstance", run_lean))

    assert result["outputs"]["accepted"] is True
    assert "exact inferInstance" in seen[1]


def test_the_repair_changes_only_the_wrapper(workdir):
    """The mathematical expression must survive untouched."""
    run_lean, seen = compiler(accepts="exact hs.exists_isMaxOn hne hf")

    run(proving.try_proof(workdir, GOAL, "hs.exists_isMaxOn hne hf", run_lean))

    assert seen[1].rstrip().endswith("by\n  exact hs.exists_isMaxOn hne hf")


def test_exactly_one_extra_compilation(workdir):
    run_lean, seen = compiler(accepts="exact hs.image hf")

    result = run(proving.try_proof(workdir, GOAL, "hs.image hf", run_lean))

    assert len(seen) == 2
    assert result["outputs"]["compiles_used"] == 1


def test_a_failed_repair_returns_the_original_rejection(workdir):
    """The term itself needs rethinking, not its wrapper."""
    run_lean, seen = compiler(accepts=None)

    result = run(proving.try_proof(workdir, GOAL, "hs.wrong_lemma hf", run_lean))

    assert result["outputs"]["accepted"] is False
    assert "REJECTED" in result["message"]
    assert "automatic repair" not in result["message"]
    assert len(seen) == 2, "more than one repair was attempted"


def test_a_failed_repair_is_still_charged(workdir):
    """The compiler ran. An uncharged compile is a hole in the budget."""
    run_lean, seen = compiler(accepts=None)

    result = run(proving.try_proof(workdir, GOAL, "hs.wrong hf", run_lean))

    assert result["outputs"]["compiles_used"] == 1


def test_a_repair_cannot_trigger_another(workdir):
    """`repair=False` on the recursive call. Without it, a repaired proof that
    also fails with `unknown tactic` would repair again, unbounded."""
    run_lean, seen = compiler(accepts=None)

    run(proving.try_proof(workdir, GOAL, "some.term arg", run_lean))

    assert len(seen) == 2, f"{len(seen) - 1} repairs; expected at most 1"


def test_no_repair_when_the_error_is_something_else(workdir):
    """A term rejected for unsolved goals is a mathematical failure, and
    wrapping it changes nothing."""
    run_lean, seen = compiler(accepts=None, error="f.lean:1:1: error: unsolved goals")

    run(proving.try_proof(workdir, GOAL, "hs.image hf", run_lean))

    assert len(seen) == 1


def test_a_generic_tactic_is_never_repaired(workdir):
    run_lean, seen = compiler(accepts=None)

    run(proving.try_proof(workdir, GOAL, "by aesop", run_lean))

    assert len(seen) == 1, "a real tactic was wrapped in `exact`"


# --------------------------------------------- the guards still hold
def test_a_placeholder_is_still_refused_before_any_compile(workdir):
    run_lean, seen = compiler(accepts="exact")

    result = run(proving.try_proof(workdir, GOAL, "by sorry", run_lean))

    assert result["error"] == "placeholder_proof"
    assert seen == []


@pytest.mark.parametrize("outcome", [LeanOutcome.INCOMPLETE, LeanOutcome.CHEATED])
def test_a_repair_cannot_launder_a_cheating_proof(workdir, outcome):
    """The repaired compile goes through the same `interpret`. A file that only
    compiles via `sorry` or `exact?` is not accepted because it was repaired."""
    seen = []

    async def run_lean(source):
        seen.append(source)
        if "exact" in source and "mra_goal" in source:
            return LeanResult(outcome, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.ERRORS, UNKNOWN_TACTIC)

    result = run(proving.try_proof(workdir, GOAL, "myTerm arg", run_lean))

    assert result["outputs"]["accepted"] is False
    assert not log.accepted_proof(workdir, GOAL)


def test_the_recorded_four_variant_sequence_is_unaffected(workdir):
    """num-primes-strictly-above: rcases, obtain, match, cases — all tactic-
    shaped, so none is repaired, and the fourth still wins."""
    goal = "theorem exists_prime_gt (n : ℕ) : ∃ p, Nat.Prime p ∧ n < p"
    variants = [
        "rcases Nat.exists_infinite_primes (n + 1) with ⟨p, hp1, hp2⟩\n  exact ⟨p, hp2, hp1⟩",
        "obtain ⟨p, hp1, hp2⟩ := Nat.exists_infinite_primes (n + 1)\n  use p",
        "match Nat.exists_infinite_primes (n + 1) with\n| ⟨p, hp1, hp2⟩ => exact ⟨p, hp2, hp1⟩",
        "cases Nat.exists_infinite_primes (n + 1) with\n| intro p hp => exact ⟨p, hp.2, hp.1⟩",
    ]
    run_lean, seen = compiler(accepts="cases Nat.exists_infinite_primes")

    results = [run(proving.try_proof(workdir, goal, v, run_lean)) for v in variants]

    assert results[-1]["outputs"]["accepted"] is True
    assert len(seen) == 4, f"{len(seen)} compiles for 4 variants; no repair expected"
