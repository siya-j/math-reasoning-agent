"""Attempts to make the system report a proof it does not have.

WHY THIS FILE IS DIFFERENT FROM EVERY OTHER TEST FILE HERE
----------------------------------------------------------
Every other guard in this repo was written after a real run produced a bad
result, and its test reproduces that incident. That discipline is why the
comments can say MEASURED and name a goal. It also means holes were only ever
found by accident, in production, one at a time -- the search strategy was
"wait for it to happen."

This file inverts that. Its job is to CONSTRUCT a false `proved` rather than
wait for one. Two of the attacks below succeeded when they were first written,
and the fixes they forced are noted on each.

THE PROPERTY UNDER ATTACK
-------------------------
`proof_verdict` returns PROVED only when the compiler accepted a proof of the
DECLARED goal. Attacks come in two flavours and both matter:

  * make the compiler say yes to something that is not a proof
  * make the DECLARED GOAL something other than the claim

The second is the sneakier one, and it is where both live findings were.

WHY AN INJECTED COMPILER IS THE RIGHT TOOL HERE
-----------------------------------------------
Each attack states what a REAL Lean would answer, and asks what the system
does with that answer. `by native_decide` really does compile; `exact h`
against `(h : P) : P` really does compile. The question under test is never
"what does Lean say" but "what does this system conclude from what Lean
said", and that is answerable without a toolchain -- the same reasoning
`verifiers/lean_verifier.py` gives for injecting its runner.
"""

import asyncio
import tempfile

import pytest

from math_v2.core import log, proving, verdict
from verifiers.lean_runner import (
    LeanOutcome,
    LeanResult,
    cheating_devices,
    has_placeholder,
)

ELABORATES = "warning: declaration uses 'sorry'"


def run(coro):
    return asyncio.run(coro)


def lean(outcome, output=""):
    seen = []

    async def runner(source):
        seen.append(source)
        return LeanResult(outcome, output)

    runner.seen = seen
    return runner


@pytest.fixture
def workdir():
    return tempfile.mkdtemp()


def declare(workdir, statement):
    """Get `statement` accepted as the declared goal, as a real run would."""
    return run(proving.check_statement(
        workdir, statement, lean(LeanOutcome.INCOMPLETE, ELABORATES)))


def outcome_of(workdir):
    return verdict.proof_verdict(workdir, log.declared_goal(workdir))["outcome"]


# ===================================================================
# ATTACK 1 — the goal restated as its own hypothesis.  SUCCEEDED once.
# ===================================================================
CIRCULAR = ("theorem g (h : Irrational (Real.sqrt 2)) "
            ": Irrational (Real.sqrt 2)")


def test_a_statement_that_assumes_its_conclusion_is_refused(workdir):
    """THE FIRST LIVE FINDING. This elaborates, `exact h` closes it, and it
    was reported PROVED with nothing in the way: `refuse` passed it,
    `faithfulness_failure` passed it (that lint is arithmetic, and no number
    differs), and `says_nothing` passed it because the conclusion is not
    `True`. It is `exercise_1_19b`'s substitution in a form that still
    mentions the real objects, so it reads plausible in a results file."""
    result = declare(workdir, CIRCULAR)

    assert result["error"] == "assumes_conclusion"


def test_the_circular_statement_cannot_reach_proved_through_try_proof(workdir):
    """Refusing it in `check_statement` ALONE was not enough, and the attack
    proved it: the check was refused and the run still reported `proved`,
    because `try_proof` takes a `statement=` of its own, `declared_goal` reads
    the last statement check whatever its status, and `accepted_proof` then
    found a TRUE proof record for it."""
    declare(workdir, CIRCULAR)
    compiler = lean(LeanOutcome.COMPILED)

    result = run(proving.try_proof(workdir, CIRCULAR, "exact h", compiler))

    assert result["error"] == "assumes_conclusion"
    assert compiler.seen == [], "a self-assuming statement reached Lean"
    assert outcome_of(workdir) == verdict.NOT_FORMALIZED


def test_the_circular_statement_cannot_reach_proved_through_a_skeleton(workdir):
    declare(workdir, CIRCULAR)
    compiler = lean(LeanOutcome.COMPILED)

    result = run(proving.try_skeleton(
        workdir, CIRCULAR, "by\n  have h1 : True := by sorry\n  exact h",
        compiler))

    assert result["error"] == "assumes_conclusion"
    assert compiler.seen == []


def test_nested_brackets_do_not_hide_the_circularity(workdir):
    """The first implementation used a character-class regex and returned
    False on the attack above, because a regex cannot express balanced
    brackets -- it read `(h : Irrational (Real.sqrt 2))` as ending at the
    inner `)`. `binder_groups` scans depth instead."""
    assert proving.assumes_its_own_conclusion(CIRCULAR)
    assert proving.assumes_its_own_conclusion(
        "theorem g [inst : Foo (Bar (Baz x))] : Foo (Bar (Baz x))")


@pytest.mark.parametrize("statement", [
    "theorem g (n : Nat) (h : n > 0) : n + 0 = n",
    "theorem g (h : P) : P ∨ Q",
    "theorem g (h : P → Q) : Q",
    ("theorem putnam_1962_b1 (p : ℕ → ℝ → ℝ) (x y : ℝ) (n : ℕ) "
     "(h0 : p 0 = fun x : ℝ => 1) : p n (x+y) = 0"),
])
def test_honest_statements_are_not_refused(statement):
    """The negative control, and it carries the real weight: a false refusal
    here costs a formalisation the agent cannot retry, so the guard is
    deliberately an exact text match rather than any reasoning about
    implication. The last case is a real PutnamBench signature."""
    assert not proving.assumes_its_own_conclusion(statement)


# ===================================================================
# ATTACK 2 — native_decide.  SUCCEEDED once.
# ===================================================================
def test_native_decide_is_treated_as_a_cheating_device():
    """THE SECOND LIVE FINDING. `by native_decide` compiles with no warning,
    so `interpret` read COMPILED and the run reported PROVED. It closes the
    goal on the COMPILER's evaluation rather than the kernel's, adding
    `Lean.ofReduceBool` to the proof's axioms -- and Lean's own history
    contains proofs of False obtained that way.

    It belongs in `cheating_devices` because that function's stated subject is
    already "constructs that make a file compile without proving the
    theorem"; this was simply missing from the list."""
    assert cheating_devices("by native_decide")
    assert cheating_devices("by exact ofReduceBool _")


def test_plain_decide_is_not_a_cheating_device():
    """`decide` reduces IN THE KERNEL and is an ordinary tactic. Refusing it
    would break honest proofs, so the distinction is the whole point."""
    assert cheating_devices("by decide") == []
    assert cheating_devices("by norm_num") == []
    assert cheating_devices("by simp") == []


def test_a_cheated_compile_is_not_a_proof(workdir):
    """The end of the chain: whatever `run_lean` flags as CHEATED must not
    become TRUE. `_util._classify` applies the anti-cheat on both backends on
    the way back, "before anything reaches the model"."""
    statement = "theorem g : (2:Nat) + 2 = 4"
    declare(workdir, statement)

    run(proving.try_proof(workdir, statement, "by native_decide",
                          lean(LeanOutcome.CHEATED, "`native_decide`")))

    assert outcome_of(workdir) != verdict.PROVED


# ===================================================================
# ATTACKS THAT HELD — defence in depth, confirmed rather than assumed
# ===================================================================
def test_sorryAx_slips_the_text_guard_but_the_compiler_catches_it(workdir):
    """An honest record of a guard that is weaker than it looks, and of the
    layer that saves it.

    `_PLACEHOLDER` is `\\b(sorry|admit)\\b`, and `\\b` fails between "sorry"
    and "Ax", so `has_placeholder("exact sorryAx _")` is False -- the text
    guard does NOT catch it. What catches it is that real Lean emits
    "declaration uses 'sorry'" for it, which `_uses_placeholder` reads off the
    OUTPUT rather than the source. Two independent checks, and only the second
    one holds here. Recorded so nobody later "simplifies" away the
    output-based check believing the regex covers it."""
    assert has_placeholder("exact sorryAx _") is False

    statement = "theorem g : (2:Nat) + 2 = 5"
    declare(workdir, statement)
    run(proving.try_proof(workdir, statement, "exact sorryAx _",
                          lean(LeanOutcome.INCOMPLETE, ELABORATES)))

    assert outcome_of(workdir) != verdict.PROVED


def test_a_proof_of_a_diversion_does_not_prove_the_goal(workdir):
    """`try_proof(statement=...)` compiles against something else on purpose.
    That must never become the declared goal."""
    real = "theorem g : Irrational (Real.sqrt 2)"
    declare(workdir, real)

    run(proving.try_proof(workdir, "theorem easy : (2:Nat) + 2 = 4",
                          "by norm_num", lean(LeanOutcome.COMPILED)))

    assert log.declared_goal(workdir) == real
    assert outcome_of(workdir) != verdict.PROVED


def test_a_proved_lemma_is_not_a_proved_goal(workdir):
    statement = "theorem g : Irrational (Real.sqrt 2)"
    declare(workdir, statement)

    run(proving.try_lemma(workdir, "lemma helper (n : Nat) : n + 0 = n",
                          "by simp", lean(LeanOutcome.COMPILED)))

    assert outcome_of(workdir) != verdict.PROVED


def test_a_typechecking_skeleton_is_not_a_proof(workdir):
    """A skeleton full of `sorry` typechecks. That establishes the SHAPE of an
    argument and nothing else, and is recorded UNKNOWN whatever Lean says."""
    statement = "theorem g : Irrational (Real.sqrt 2)"
    declare(workdir, statement)

    run(proving.try_skeleton(
        workdir, statement, "by\n  have h1 : (1:Nat) + 1 = 2 := by sorry\n  sorry",
        lean(LeanOutcome.INCOMPLETE, ELABORATES), fill_budget=0))

    assert outcome_of(workdir) != verdict.PROVED


def test_claiming_proved_with_nothing_recorded_is_refused(workdir):
    """The last line of defence, and the one the whole architecture rests on:
    prose is not evidence."""
    declare(workdir, "theorem g : Irrational (Real.sqrt 2)")
    decision = verdict.proof_verdict(workdir, log.declared_goal(workdir))

    assert verdict.refuse("proved", decision)


def test_an_empty_declared_goal_cannot_match_any_accepted_proof(workdir):
    """`log.accepted_proof("")` treats an empty statement as "no filter", so a
    verdict computed on one would match ANY accepted record. `proof_verdict`
    refuses outright instead."""
    run(proving.try_proof(workdir, "theorem anything : (2:Nat) + 2 = 4",
                          "by norm_num", lean(LeanOutcome.COMPILED)))

    assert verdict.proof_verdict(workdir, "")["outcome"] == verdict.NOT_FORMALIZED


def test_a_conclusion_of_True_is_still_refused(workdir):
    """`says_nothing`, retested from the attacker's side rather than the
    incident's: the original `exercise_1_19b` substitution must stay closed."""
    result = declare(workdir, "theorem g (z : ℂ) (h : z = 1) : True")

    assert result["ok"] is False


# ===================================================================
# The control: an honest proof must still be reported
# ===================================================================
def test_an_honest_proof_still_reaches_proved(workdir):
    """Every guard above is worthless if it also blocks real work. This is the
    test that keeps the rest from being satisfied by refusing everything."""
    statement = "theorem g : (2:Nat) + 2 = 4"
    declare(workdir, statement)

    result = run(proving.try_proof(workdir, statement, "by norm_num",
                                   lean(LeanOutcome.COMPILED)))

    assert result["outputs"]["accepted"] is True
    assert outcome_of(workdir) == verdict.PROVED
