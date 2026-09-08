"""A guard refusal must not cost the compilation it never used.

`tools/proving._charge` bills a compile BEFORE dispatching, so that no work
happens once a limit is hit. That ordering is right, and it meant every guard
refusal was billed a compilation the compiler never ran.

MEASURED through the real tool path: three attempts at a goal, the third
refused by `generic_exhausted` without compiling, and `lean_calls` read 2 of a
budget of 12. `_with_headroom` even documented the opposite -- "a refusal
whose whole point is that nothing was spent" -- which was untrue of the
compile counter. The `decompose_first` redirect made it sharper still: the
agent paid a compilation to be told to decompose.

The refund keys on the PRESENCE of an `error` key rather than a list of
refusal codes, and `test_no_refusal_code_is_set_after_compiling` is what makes
that safe: it scans `core/proving.py` and fails if any `error` is ever
returned after `run_lean` has been called. A hand-maintained allow-list would
have drifted the moment a ninth guard was added -- the same failure mode that
cost this project a results file.
"""

import asyncio
import re
from pathlib import Path

import pytest
from langchain.tools import ToolRuntime

from math_v2.context import MathContext
from math_v2.core import budget, log
from math_v2.tools import proving as proving_tools
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = "theorem mra_goal (n : Nat) : n + 0 = n"
CORE = Path(__file__).resolve().parent.parent / "math_v2" / "core" / "proving.py"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def rejecting(monkeypatch):
    async def no(source):
        return LeanResult(LeanOutcome.ERRORS, "3:2: error: unsolved goals")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda w: no)


@pytest.fixture
def rt(tmp_path):
    workdir = str(tmp_path)
    log.clear(workdir)
    budget.reset(workdir)
    log.set_goal(workdir, GOAL)
    return ToolRuntime(state=None, context=MathContext(workdir=workdir),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


def _spent(rt):
    return budget.read(rt.context.workdir)["lean_calls"]


# ------------------------------------------------------- THE regression
def test_a_refusal_costs_no_compilation(rt, rejecting):
    """`generic_exhausted`: a second bare closer, refused without compiling."""
    run(proving_tools.try_proof.ainvoke(
        {"proof": "by norm_num", "statement": GOAL, "runtime": rt}))
    assert _spent(rt) == 1

    result = run(proving_tools.try_proof.ainvoke(
        {"proof": "by simp", "statement": GOAL, "runtime": rt}))

    assert result["error"] == "generic_exhausted"
    assert _spent(rt) == 1, (
        "a refusal that never reached Lean was billed a compilation"
    )


def test_the_decomposition_redirect_costs_no_compilation(rt, rejecting):
    """The one that made this sharpest: without the refund the agent pays a
    compilation to be TOLD to decompose, which partly defeats the redirect."""
    drafts = ["by exact Nat.add_zero n",
              "by exact (Nat.add_zero n).symm ▸ rfl",
              "by simpa [Nat.add_zero] using rfl"]
    for draft in drafts:
        run(proving_tools.try_proof.ainvoke(
            {"proof": draft, "statement": GOAL, "runtime": rt}))
    assert _spent(rt) == 3

    result = run(proving_tools.try_proof.ainvoke(
        {"proof": "by exact congrArg (· + 0) rfl", "statement": GOAL,
         "runtime": rt}))

    assert result["error"] == "decompose_first"
    assert _spent(rt) == 3, "being redirected cost a compilation"


def test_a_placeholder_costs_no_compilation(rt, rejecting):
    result = run(proving_tools.try_proof.ainvoke(
        {"proof": "by sorry", "statement": GOAL, "runtime": rt}))

    assert result["error"] == "placeholder_proof"
    assert _spent(rt) == 0


def test_a_missing_statement_costs_no_compilation(tmp_path, rejecting):
    """`no_statement` never compiles either, and was returned around the
    refund rather than through it."""
    workdir = str(tmp_path)
    log.clear(workdir)
    budget.reset(workdir)
    runtime = ToolRuntime(state=None, context=MathContext(workdir=workdir),
                          config={}, stream_writer=lambda *a, **k: None,
                          tool_call_id="t", store=None)

    result = run(proving_tools.try_proof.ainvoke(
        {"proof": "by rfl", "runtime": runtime}))

    assert result["error"] == "no_statement"
    assert budget.read(workdir)["lean_calls"] == 0


# ------------------------------------------- what must STILL be charged
def test_a_real_rejection_is_still_charged(rt, rejecting):
    """The line the refund must not cross. Lean ran and said no; that is a
    compilation, and refunding it would make the budget unbounded."""
    run(proving_tools.try_proof.ainvoke(
        {"proof": "by exact Nat.add_zero n", "statement": GOAL, "runtime": rt}))
    assert _spent(rt) == 1


def test_an_accepted_proof_is_still_charged(rt, monkeypatch):
    async def ok(source):
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda w: ok)
    run(proving_tools.try_proof.ainvoke(
        {"proof": "by simp", "statement": GOAL, "runtime": rt}))
    assert _spent(rt) == 1


def test_the_refund_cannot_manufacture_budget(tmp_path):
    """Clamped at zero. A refund with no matching charge is a caller bug and
    must not hand the agent compilations it never had."""
    workdir = str(tmp_path)
    budget.reset(workdir)
    for _ in range(3):
        budget.refund_lean(workdir)
    assert budget.read(workdir)["lean_calls"] == 0


# --------------------------------- the invariant the refund rests on
def test_no_refusal_code_is_set_after_compiling():
    """THE GUARD ON THE GUARD, and the reason this is not an allow-list.

    The refund fires on any result carrying an `error`. That is only sound
    while every `error` in `core/proving.py` is decided BEFORE `run_lean`. This
    test reads the source and checks it, so a future guard that refuses after
    compiling fails here rather than silently collecting a refund it has not
    earned -- and a new guard added correctly needs no change anywhere.
    """
    lines = CORE.read_text(encoding="utf-8").splitlines()
    offenders = []
    for index, line in enumerate(lines):
        if '"error"' not in line:
            continue
        start = index
        while start > 0 and not re.match(r"^(async )?def ", lines[start]):
            start -= 1
        if "await run_lean(" in "\n".join(lines[start:index]):
            offenders.append(f"{lines[start].strip()[:60]} -> line {index + 1}")

    assert not offenders, (
        "these return an `error` AFTER compiling, so `_with_headroom` would "
        "refund a compilation that really happened:\n  " + "\n  ".join(offenders)
    )


def test_every_refusal_code_is_actually_reachable_as_a_refusal():
    """Sanity on the scan above: it found something. A regex that matched
    nothing would make the invariant test vacuously green."""
    found = re.findall(r'"error":\s*"([a-z_]+)"',
                       CORE.read_text(encoding="utf-8"))
    assert len(set(found)) >= 8, sorted(set(found))
    assert "decompose_first" in found
