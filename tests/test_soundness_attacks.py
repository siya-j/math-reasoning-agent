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


def _runtime(workdir):
    from langchain.tools import ToolRuntime

    from math_v2.context import MathContext

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


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
def test_sorryAx_is_refused_by_the_text_guard_and_by_the_compiler(workdir):
    """THIS TEST USED TO ASSERT THE OPPOSITE, and the opposite was a live
    soundness hole.

    It recorded, as an honest limitation, that `has_placeholder("exact
    sorryAx _")` is False -- true, because `\\b(sorry|admit)\\b` cannot match
    across "sorry" and "Ax" -- and claimed the output check caught it
    instead: real Lean emits "declaration uses 'sorry'", which
    `_uses_placeholder` reads off the OUTPUT. Two independent layers, only the
    second holding.

    MEASURED against real Lean 4.33.0, the first time `tests/test_lean_real.py`
    ran in full: THE SECOND LAYER DID NOT HOLD EITHER. The marker was a fixed
    string with STRAIGHT quotes and Lean writes BACKTICKS -- "declaration uses
    `sorry`" -- so it matched nothing. With a well-formed call,
    `by exact sorryAx _ false`, the system returned LeanOutcome.COMPILED and
    VerificationStatus.TRUE. A proof of any theorem, accepted.

    Note also that this test's own snippet, `exact sorryAx _`, does not
    typecheck: `sorryAx` takes `(α) (synthetic : Bool := false)`, so one
    argument leaves it partially applied. The test was passing on an
    expression Lean would have rejected outright, which is why the hole
    underneath went unnoticed.

    Both layers are fixed, and this now asserts the property rather than
    which layer delivers it -- depending on exactly one layer is what made
    this possible.
    """
    assert has_placeholder("exact sorryAx _ false") is True, (
        "the text guard no longer names sorryAx; `by exact sorryAx _ false` "
        "proves any theorem"
    )

    statement = "theorem g : (2:Nat) + 2 = 5"
    declare(workdir, statement)
    run(proving.try_proof(workdir, statement, "exact sorryAx _ false",
                          lean(LeanOutcome.COMPILED, ELABORATES)))

    # COMPILED is passed in deliberately: even if the compiler reported a
    # clean success, this must not be accepted.
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


# ===================================================================
# A refusal that never compiled must not spend a formalisation attempt
# ===================================================================
def test_a_refused_statement_check_is_refunded(workdir):
    """MEASURED on `lin-vector-space-basis` in the mixed run. The agent had
    two statement checks. It spent the first on a formalisation using `Basis`,
    which stopped elaborating when Mathlib renamed it `Module.Basis`. It spent
    the second on a `theorem dummy : True` probe -- forbidden by the prompt
    and correctly refused by `says_nothing` WITHOUT compiling. It then
    searched, FOUND `Module.Basis`, and had no checks left to use it; it
    proved the theorem as a scratch diversion instead, which cannot score, and
    the run recorded `not_formalized` for a goal it had solved.

    Charging for a refusal punishes the agent for a probe the guard already
    refused. The guard still costs it a turn; it should not also cost one of
    only two formalisation attempts.
    """
    from math_v2.core import budget
    from math_v2.tools.proving import check_statement

    budget.reset(workdir)
    rt = _runtime(workdir)

    result = run(check_statement.ainvoke(
        {"statement": "theorem dummy : True", "runtime": rt}))

    assert result["error"] == "trivial_conclusion"
    assert budget.read(workdir)["statement_checks"] == 0, (
        "a refusal that never reached Lean spent a formalisation attempt"
    )


def test_a_genuine_compiler_rejection_still_counts(workdir):
    """The control, and the line the refund must not cross: an ordinary
    rejection IS the syntax being judged, so it costs a check. Refunding that
    would make MAX_STATEMENT_CHECKS unbounded."""
    from math_v2.core import budget
    from math_v2.tools import proving as proving_tools
    from math_v2.tools.proving import check_statement

    budget.reset(workdir)

    async def rejects(source):
        return LeanResult(LeanOutcome.ERRORS, "3:2: error: unknown identifier 'Basis'")

    monkey = proving_tools.lean_runner
    proving_tools.lean_runner = lambda workdir: rejects
    try:
        run(check_statement.ainvoke(
            {"statement": "theorem t : Nonempty (Basis K V)",
             "runtime": _runtime(workdir)}))
    finally:
        proving_tools.lean_runner = monkey

    assert budget.read(workdir)["statement_checks"] == 1


# ===================================================================
# `sorryAx` proves anything, and evaded BOTH layers of the defence
# ===================================================================
def test_sorryAx_is_named_by_the_source_guard():
    """MEASURED as a live hole against real Lean 4.33.0.
    `by exact sorryAx _ false` returned LeanOutcome.COMPILED and
    VerificationStatus.TRUE -- a proof of ANY theorem, accepted.

    `sorryAx` is what `sorry` elaborates to; it is directly writable, and
    `\\b(sorry|admit)\\b` cannot match it because the word boundary fails
    between "sorry" and "Ax". Named explicitly now rather than matched with
    `sorry\\w*`, which would also flag an honest lemma whose name happens to
    begin with "sorry".
    """
    from verifiers.lean_runner import has_placeholder

    assert has_placeholder("by exact sorryAx _ false")
    assert has_placeholder("exact sorryAx (2 + 2 = 4) false")
    # ...and the honest cases stay honest.
    assert not has_placeholder("by exact Nat.add_zero n")
    assert not has_placeholder("lemma sorryless_proof : True := trivial")


def test_the_sorry_warning_is_matched_whatever_lean_quotes_it_with():
    """THE offline test that would have caught it. The marker was two fixed
    strings using STRAIGHT quotes -- "declaration uses 'sorry'" -- and Lean
    4.33.0 emits BACKTICKS. Neither literal ever matched, so the entire
    second layer of the defence silently did nothing for an unknown length of
    time.

    A claim about someone else's output format must not be a fixed string.
    """
    from verifiers.lean_runner import _SORRY_WARNING

    for wording in (
        "1:8: warning: declaration uses `sorry`",       # Lean 4.33.0
        "1:8: warning: declaration uses 'sorry'",       # older wording
        "warning: declaration uses ‘sorry’",  # typographic quotes
        "declaration uses sorry",                       # unquoted
    ):
        assert _SORRY_WARNING.search(wording), wording

    assert not _SORRY_WARNING.search("1:1: error: unknown identifier 'foo'")


def test_a_backticked_warning_alone_marks_the_proof_incomplete():
    """The decision point, fed exactly what real Lean emits: a zero-exit
    compile whose ONLY output is the backticked warning, and whose source the
    regex cannot help with because the placeholder is spelled `sorryAx`.

    `_uses_placeholder` is what `run_lean` and `math_v2.tools._util` both call
    to turn that into INCOMPLETE. Before the fix it returned False on this
    input, so the outcome was COMPILED and the verdict TRUE.
    """
    from domain.verdict import VerificationStatus
    from verifiers.lean_runner import (LeanOutcome, LeanResult,
                                       _uses_placeholder)
    from verifiers.lean_verifier import interpret

    output = "1:8: warning: declaration uses `sorry`\n"

    assert _uses_placeholder("theorem t : 2 + 2 = 4 := by trivial", output), (
        "a compile whose only output is Lean's sorry warning was read as a "
        "complete proof"
    )

    verdict = interpret(LeanResult(LeanOutcome.INCOMPLETE, output),
                        "theorem t : 2 + 2 = 4")
    assert verdict.status is not VerificationStatus.TRUE, verdict
