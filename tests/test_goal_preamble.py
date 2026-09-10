"""A goal's `open` lines have to reach the compiler, not just the model.

MEASURED, AND THE REASON THIS EXISTS. `DEFAULT_PREAMBLE` is `import Mathlib`
and nothing else. ProofNet ships every one of its 182 statements with an
`open` header, `eval/proofnet.py` documents that the statements do not
elaborate without it, and the header reached the MODEL -- inside the goal
text -- but never the COMPILER. Only 43 of 702 submitted statements carried
an `open` line.

Demonstrated against real Lean on `exercise_2_5_30`, same statement and same
Mathlib:

    without the opens   error: Function expected at card
                              but this term has type ?m.1
    with the opens      warning: declaration uses `sorry`   (it elaborates)

Eleven of the seventeen bare-name arity errors in the preserved workdirs are
on goals whose own header would have prevented them, across six goals that
between them cost 13,288,921 input tokens.
"""

import asyncio
import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from math_v2.core import budget, log, preamble, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = """Prove this Lean 4 theorem.

open Fintype Set Real Ideal Polynomial
open scoped BigOperators

theorem exercise_2_5_30 {G : Type*} [Group G] [Fintype G] (hG : card G = 3) :
  Nat.Prime (card G) := sorry

It formalises the following claim: ..."""

STATEMENT = ("theorem exercise_2_5_30 {G : Type*} [Group G] [Fintype G] "
             "(hG : card G = 3) : Nat.Prime (card G)")


def _workdir():
    path = tempfile.mkdtemp()
    log.clear(path)
    budget.reset(path)
    return path


def _capture():
    """A fake compiler that records the first source it is handed."""
    seen = {}

    async def run_lean(source):
        seen.setdefault("src", source)
        return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")

    return seen, run_lean


# ------------------------------------------------------- extraction
def test_every_open_line_is_kept_in_order():
    """`open scoped` and plain `open` are not interchangeable, and the
    benchmark's own ordering is the one its statements were tested against."""
    lines = preamble.opens_in(GOAL)
    assert lines.splitlines() == [
        "open Fintype Set Real Ideal Polynomial",
        "open scoped BigOperators",
    ]


def test_a_repeated_open_is_not_repeated():
    text = "open Real\nopen Real\nopen Filter\n"
    assert preamble.opens_in(text).splitlines() == ["open Real", "open Filter"]


def test_prose_mentioning_open_is_not_mistaken_for_a_directive():
    """The goal text is prose wrapped around Lean. Only a line that STARTS
    with `open` is a directive; a sentence containing the word is not."""
    assert preamble.opens_in("This proof is open to a shorter argument.") == ""
    assert preamble.opens_in("  the set is open in X") == ""


def test_a_goal_with_no_opens_stores_nothing():
    assert preamble.opens_in("theorem t : 1 = 1 := sorry") == ""


# ------------------------------------------------------- it reaches Lean
def test_the_opens_reach_the_statement_check():
    """The statement check is where 72% of the arity failures were, and it
    does NOT go through `full_statement`, so it had to be fixed at the
    preamble rather than there."""
    workdir = _workdir()
    preamble.remember(workdir, GOAL)
    seen, run_lean = _capture()

    asyncio.run(proving.check_statement(workdir, STATEMENT, run_lean))

    assert "open Fintype Set Real Ideal Polynomial" in seen["src"]
    assert "open scoped BigOperators" in seen["src"]
    assert seen["src"].index("open Fintype") < seen["src"].index("theorem"), (
        "the opens must precede the declaration or Lean never sees them")


def test_the_opens_reach_a_proof_attempt_too():
    """Fixing only the statement check would move the failure one step
    later: the signature elaborates, then `try_proof` compiles it against a
    preamble that no longer resolves the same names."""
    workdir = _workdir()
    preamble.remember(workdir, GOAL)
    seen, run_lean = _capture()

    asyncio.run(proving.check_statement(workdir, STATEMENT, run_lean))
    seen.clear()
    asyncio.run(proving.try_proof(workdir, STATEMENT, "by decide", run_lean))

    assert seen, "try_proof never reached the compiler"
    assert "open Fintype" in seen["src"]


def test_without_a_stored_preamble_the_source_is_byte_identical():
    """The fallback must be the behaviour every existing result was produced
    under, or every number on record becomes incomparable."""
    from verifiers.lean_verifier import build_source

    workdir = _workdir()          # nothing remembered
    seen, run_lean = _capture()
    asyncio.run(proving.check_statement(workdir, STATEMENT, run_lean))

    assert seen["src"] == build_source(STATEMENT, "sorry")


def test_an_unwritable_workdir_costs_the_opens_and_not_the_run():
    """A guard's failure mode must be "nothing is proved", never "the agent
    cannot run" -- the rule `log.read` already follows."""
    assert preamble.remember("/nonexistent/path/xyz", GOAL) is not None
    assert preamble.source("/nonexistent/path/xyz") == preamble.BASE


# ------------------------------------------------------- the drift guard
def test_no_compile_in_core_proving_bypasses_the_preamble():
    """THE GUARD ON THE FIX ITSELF.

    Eight call sites were routed through `_source`. Nothing stops a ninth
    being added that calls `build_source` directly, and it would fail only on
    the goals whose header matters -- silently, and expensively. This repo has
    already paid three times for a hand-maintained set drifting out of step.
    """
    source = (Path(proving.__file__)).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    calls = re.findall(r"\bbuild_source\(", body)
    # Exactly one: the single call inside `_source` itself.
    assert len(calls) == 1, (
        f"{len(calls)} direct build_source calls in core/proving.py; every "
        "compile must go through _source(workdir, ...) so the goal's opens "
        "are applied")


def test_the_harness_remembers_the_opens_before_the_agent_runs():
    """Captured at setup, because the tools only receive a workdir -- the
    goal text is not in `MathContext` and cannot be recovered later."""
    harness = (Path(proving.__file__).parent.parent / "harness.py").read_text(
        encoding="utf-8")
    assert "preamble.remember(workdir, goal)" in harness
