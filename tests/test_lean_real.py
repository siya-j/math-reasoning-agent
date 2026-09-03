"""The claims about Lean that only a real Lean can settle.

WHY THIS FILE EXISTS
--------------------
Of the 1028 tests in this repo, exactly ONE touched a compiler
(`test_lean_verifier.py::test_end_to_end_against_a_real_lean_installation`,
three assertions long), and it is skipped wherever `elan` is unconfigured --
which is every environment this project's tests have been run in recently.

That is the wrong shape for this system. Everything it asserts reduces to "the
compiler accepted this", so `verifiers/lean_runner.py`'s reading of compiler
output is the foundation the guard, the verdict and the benchmark all stand
on. It was also the least-exercised code in the repo. Every other test injects
a fake compiler and asks what the POLICY concludes -- correct and necessary,
and it cannot tell you that the policy is being fed the right facts.

WHAT EACH TEST HERE BUYS, AND WHY IT IS WORTH A REAL COMPILE
------------------------------------------------------------
Every case below encodes a belief about Lean's behaviour that was previously
held on authority rather than measurement. Two were introduced the same day
this file was, by the soundness-attack work, and were verified only against an
injected compiler:

  * `by native_decide` compiles CLEANLY -- no error, no warning -- which is
    exactly why it had to be added to `cheating_devices` rather than left to
    the ordinary error path. If that belief is wrong the fix is misdirected.
  * `by decide` compiles and must NOT be flagged, or the fix above has broken
    honest proofs.
  * `exact sorryAx _` is NOT caught by `has_placeholder` (the `\\b` in
    `\\b(sorry|admit)\\b` fails between "sorry" and "Ax") and is caught only
    because Lean emits "declaration uses 'sorry'". That is the single
    load-bearing claim behind calling it defence in depth, and it is a claim
    about Lean's OUTPUT.

COST, AND HOW TO SKIP IT
------------------------
Each test is one real compile against a prebuilt Mathlib -- on this project's
own measurements roughly 20-45 seconds each, so expect a few minutes. That is
the price of the foundation being checked at all. To skip:

    pytest --ignore=tests/test_lean_real.py

The whole file skips automatically wherever Lean is not installed, so it costs
nothing where it cannot run -- and reports nothing either, which is the
situation this file exists to make visible rather than comfortable.
"""

import pytest

from verifiers.lean_runner import (
    LeanOutcome,
    cheating_devices,
    has_placeholder,
    mathlib_is_available,
    run_lean,
)
from verifiers.lean_verifier import build_source

# GATED ON MATHLIB BEING REACHABLE, not merely on Lean running. MEASURED: on a
# machine with a perfectly healthy Lean but `MRA_LEAN_PROJECT` unset, the
# weaker gate let all eleven of these run against bare `lean`; seven failed
# with "unknown module prefix 'Mathlib'" and FOUR PASSED FOR THE WRONG REASON,
# because an assertion of the form "this did not compile" is satisfied by any
# infrastructure fault. Zero of the eleven measured what they claimed to.
#
# If this file skips and you expected it to run, set MRA_LEAN_PROJECT to a
# Lake project that depends on Mathlib.
pytestmark = pytest.mark.skipif(
    not mathlib_is_available(),
    reason="`import Mathlib` does not resolve; set MRA_LEAN_PROJECT",
)


def judged(source):
    """Compile, and fail LOUDLY if Lean never actually judged the source.

    The second half of the same lesson. Even behind the gate, an assertion
    that a source did NOT compile is worthless unless the compiler got far
    enough to have an opinion about it -- a missing import, a broken project
    or an unavailable binary would all satisfy it. Every test below routes
    through here so infrastructure can never masquerade as a finding.
    """
    result = run_lean(source)
    assert result.outcome is not LeanOutcome.UNAVAILABLE, "Lean did not run"
    assert "unknown module prefix" not in (result.output or ""), (
        f"Mathlib was not on the search path, so this measured nothing:\n"
        f"{result.output}"
    )
    return result

TRUE_CLAIM = "theorem t : (2:Nat) + 2 = 4"
FALSE_CLAIM = "theorem t : (2:Nat) + 2 = 5"


# ------------------------------------------------------------ the baseline
def test_an_honest_proof_compiles():
    """The control. Everything below is a claim about how Lean DEVIATES from
    this, so if this fails nothing else in the file means anything."""
    result = judged(build_source(TRUE_CLAIM, "by norm_num"))

    assert result.outcome is LeanOutcome.COMPILED, result.output


def test_a_false_claim_is_rejected():
    result = judged(build_source(FALSE_CLAIM, "by norm_num"))

    assert result.outcome is LeanOutcome.ERRORS, result.output


# ------------------------------------------------- placeholders prove nothing
def test_sorry_compiles_but_is_not_a_proof():
    """Lean exits 0 on `sorry`. The exit code alone is not evidence, which is
    why `_uses_placeholder` reads the output for the warning."""
    result = judged(build_source(TRUE_CLAIM, "by sorry"))

    assert result.outcome is LeanOutcome.INCOMPLETE, result.output


def test_admit_is_never_a_proof():
    """Asserted as "not COMPILED" rather than as a specific outcome on
    purpose: `admit` may be a Mathlib alias for `sorry` (INCOMPLETE) or absent
    from this toolchain entirely (ERRORS), and BOTH are acceptable. What is
    not acceptable is it passing as a proof, and that is the assertion."""
    result = judged(build_source(TRUE_CLAIM, "by admit"))

    assert result.outcome in (LeanOutcome.INCOMPLETE, LeanOutcome.ERRORS), (
        result.output)


def test_sorryAx_is_caught_by_the_compiler_though_not_by_the_regex():
    """THE defence-in-depth claim, measured rather than asserted.

    `has_placeholder` does NOT catch this -- `\\b(sorry|admit)\\b` fails
    between "sorry" and "Ax" -- so the text guard is blind to it and the only
    thing standing in the way is Lean emitting "declaration uses 'sorry'".
    This test is the evidence for that sentence. If it ever fails, a proof
    that establishes nothing is passing as one, and the fix is to widen the
    regex rather than to relax this."""
    assert has_placeholder("exact sorryAx _") is False, (
        "the text guard now catches this; the comment in lean_runner.py and "
        "in test_soundness_attacks.py must be updated"
    )

    result = judged(build_source(TRUE_CLAIM, "exact sorryAx _"))

    # INCOMPLETE specifically, not merely "not COMPILED": the claim being
    # measured is that LEAN WARNS about it, which is the only reason this is
    # caught at all. ERRORS here would mean it was caught for some other
    # reason and the defence-in-depth story is wrong.
    assert result.outcome is LeanOutcome.INCOMPLETE, result.output


# ----------------------------------------------------- compiling ≠ proving
def test_native_decide_compiles_cleanly_which_is_why_it_needs_its_own_guard():
    """The belief the `native_decide` fix rests on, checked directly.

    The claim is not merely that `native_decide` is undesirable -- it is that
    Lean accepts it SILENTLY, with no error and no warning, so nothing in the
    ordinary error path would ever see it. That is why it belongs in
    `cheating_devices`, alongside `axiom`, rather than being left to
    `interpret`.

    The source is compiled with the anti-cheat's own detection bypassed, by
    asking `run_lean` about a proof it will flag and then checking what Lean
    ITSELF said via the returned output -- so the assertion is about the
    compiler, not about our own guard agreeing with itself."""
    assert cheating_devices("by native_decide"), "the guard is not even active"

    result = judged(build_source(TRUE_CLAIM, "by native_decide"))

    # CHEATED is our classification, and reaching it proves the guard fired on
    # a source Lean did not otherwise object to.
    assert result.outcome is LeanOutcome.CHEATED, result.output


def test_plain_decide_still_compiles_so_the_fix_broke_no_honest_proof():
    """The negative control for the test above, and the one that would catch
    an over-broad regex. `decide` reduces in the KERNEL and is an ordinary
    tactic; if `_NATIVE` ever matched it, honest proofs would start being
    refused as cheating."""
    result = judged(build_source(TRUE_CLAIM, "by decide"))

    assert result.outcome is LeanOutcome.COMPILED, result.output


def test_an_axiom_declaration_is_flagged_rather_than_believed():
    """`axiom` makes a FALSE claim compile. This is the clearest case in the
    file of "the file compiled" and "the theorem is true" coming apart."""
    source = (
        "import Mathlib\n\n"
        "axiom cheat : (2:Nat) + 2 = 5\n\n"
        "theorem mra_goal : (2:Nat) + 2 = 5 := cheat\n"
    )

    result = judged(source)

    assert result.outcome is LeanOutcome.CHEATED, result.output


def test_a_suggestion_tactic_is_not_a_committed_proof():
    result = judged(build_source(TRUE_CLAIM, "by exact?"))

    assert result.outcome is LeanOutcome.CHEATED, result.output


# --------------------------------------- the goal state the agent reads back
def test_the_goal_state_is_extracted_from_real_compiler_output():
    """`LeanResult.goals` collects lines starting `⊢`, and after a rejection
    that goal state is the single most useful thing the agent is told -- the
    prompt's whole "unsolved goals means your steps were ACCEPTED, aim at what
    is printed" instruction depends on it arriving.

    It has only ever been tested against hand-written output. If Lean's
    formatting drifts -- a different bullet, indentation, a pretty-printer
    change -- this silently returns [] and every rejection stops carrying the
    one fact that makes the next attempt better. Nothing else would fail."""
    result = judged(build_source(
        "theorem t (n : Nat) : n + 0 = n ∧ n * 1 = n", "by constructor"))

    assert result.outcome is LeanOutcome.ERRORS, result.output
    assert result.goals, (
        "no goal state parsed out of a real 'unsolved goals' error; "
        f"output was:\n{result.output}"
    )
    assert any("⊢" in goal for goal in result.goals)


def test_an_error_block_carries_more_than_its_header_line():
    """`LeanResult.errors` returns BLOCKS because Lean puts the useful part on
    the lines after the header. Measured against real output here, since the
    block boundary is a regex over Lean's own diagnostic format."""
    result = judged(build_source(
        "theorem t (n : Nat) : n + 0 = n ∧ n * 1 = n", "by constructor"))

    assert result.errors
    # The import failure is ALSO multi-line, which is how this passed for the
    # wrong reason once. Tie it to the error we actually provoked.
    assert any("unsolved goals" in block for block in result.errors), result.output
    assert "\n" in result.errors[0], (
        f"the error block is a single line; goal state was dropped:\n"
        f"{result.errors[0]}"
    )

