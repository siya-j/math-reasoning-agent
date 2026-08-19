"""A statement is refuted by the COMPILER or it is not refuted at all.

WHAT THIS FILE PROTECTS
-----------------------
`statement_suspect` was the one terminal claim in this system decided by prose.
Every other one is checked against a recorded tool execution — that asymmetry
is the whole reason the architecture exists — and this one ended the run on the
model's say-so.

Measured on proofnet `exercise_1_13a`: the agent was RIGHT (the ProofNet port
of Stein & Shakarchi 1.13a drops connectedness, so f can be a different
constant on each component) and it had the counterexample, Ω = D(0,1) ∪ D(3,1).
It never formalised it. The finding was recorded as prose and scored as a
failure to prove.

So there are now two states, and the difference between them is a compilation:

    REFUTED             Lean accepted a proof of the negation. A result.
    SUSPECT_STATEMENT   the agent said so. A diagnostic, and still unproved.

The tests below exist to keep the second from ever becoming the first without
the compiler. `test_no_cheating_route_can_establish_a_refutation` is the
load-bearing one: if the anti-cheat is ever routed around, refutation becomes a
free exit from any goal the prover cannot close, and the proof rate silently
starts measuring something else.
"""

import asyncio

import pytest

from eval.proof_metrics import ProofOutcome, classify
from domain.proof import ProofRun
from math_v2.core import log, proving, verdict
from verifiers.lean_runner import LeanOutcome, LeanResult

# `¬ (∀ z, f z = 0)` in the shape the tool requires: a named theorem whose
# conclusion is a negation.
NEGATION = "theorem mra_refutation : ¬ (∀ n : ℕ, n = 0)"
GOAL = "theorem mra_goal : ∀ n : ℕ, n = 0"


def run(coro):
    return asyncio.run(coro)


def compiler(outcome, output=""):
    """A fake Lean that returns one verdict and records what it was asked."""
    seen = []

    async def run_lean(source):
        seen.append(source)
        return LeanResult(outcome, output)

    return run_lean, seen


@pytest.fixture
def workdir(tmp_path):
    log.clear(str(tmp_path))
    return str(tmp_path)


# ------------------------------------------------------------ 1. it verifies
def test_a_compiled_negation_is_refuted(workdir):
    run_lean, seen = compiler(LeanOutcome.COMPILED)

    result = run(proving.try_refutation(workdir, NEGATION, "by simp", run_lean))

    assert result["outputs"]["refuted"] is True
    assert len(seen) == 1, "the refutation was not put to the compiler"
    assert verdict.verified_refutation(workdir)


def test_a_rejected_negation_is_not_refuted(workdir):
    """Failing to prove the negation is not evidence the statement is true, and
    it is not evidence it is false either. It stays unproved."""
    run_lean, _ = compiler(LeanOutcome.ERRORS, "f.lean:1:1: error: unsolved goals")

    result = run(proving.try_refutation(workdir, NEGATION, "by simp", run_lean))

    assert result["outputs"]["refuted"] is False
    assert verdict.verified_refutation(workdir) == {}


def test_a_refutation_may_cite_the_lemmas_already_proved(workdir):
    """A counterexample is a construction — define the object, then prove its
    properties. Forcing all of that into one declaration would make the tool
    unusable for exactly the cases it exists for."""
    log.keep_lemma(workdir, "theorem helper : True := trivial")
    run_lean, seen = compiler(LeanOutcome.COMPILED)

    run(proving.try_refutation(workdir, NEGATION, "by exact helper", run_lean))

    assert "theorem helper" in seen[0], "kept lemmas were not in the file"
    # `rename_goal` touches the LAST declaration only, so the helper keeps the
    # name the refutation cites.
    assert "theorem helper : True" in seen[0]


# --------------------------------------------------- 2. and it cannot be faked
@pytest.mark.parametrize(
    "outcome",
    [LeanOutcome.INCOMPLETE, LeanOutcome.CHEATED, LeanOutcome.TIMEOUT,
     LeanOutcome.UNAVAILABLE],
)
def test_no_cheating_route_can_establish_a_refutation(workdir, outcome):
    """THE guard. INCOMPLETE is `sorry`, CHEATED is `axiom` or `exact?`.

    Both COMPILE. Neither proves anything, and `interpret` is what knows the
    difference — this asserts the refutation path goes through it rather than
    reading the exit code.
    """
    run_lean, _ = compiler(outcome, "declaration uses 'sorry'")

    result = run(proving.try_refutation(workdir, NEGATION, "by admit", run_lean))

    assert result.get("outputs", {}).get("refuted") is not True
    assert verdict.verified_refutation(workdir) == {}


def test_a_placeholder_refutation_never_reaches_the_compiler(workdir):
    run_lean, seen = compiler(LeanOutcome.COMPILED)

    result = run(proving.try_refutation(workdir, NEGATION, "by sorry", run_lean))

    assert result["error"] == "placeholder_proof"
    assert seen == [], "a `sorry` refutation was compiled"
    assert verdict.verified_refutation(workdir) == {}


def test_proving_something_that_is_not_a_negation_refutes_nothing(workdir):
    """Without this, `theorem t : 2 + 2 = 4 := by norm_num` compiles and would
    be recorded as having refuted the goal."""
    run_lean, seen = compiler(LeanOutcome.COMPILED)

    result = run(proving.try_refutation(
        workdir, "theorem mra_x : 2 + 2 = 4", "by norm_num", run_lean))

    assert result["error"] == "not_a_negation"
    assert seen == []


@pytest.mark.parametrize(
    "statement",
    ["theorem t : ¬ (∀ n : ℕ, n = 0)",
     "theorem t : (1 : ℕ) ≠ 0",
     "theorem t : (∀ n : ℕ, n = 0) → False"],
)
def test_the_usual_ways_of_writing_a_negation_are_accepted(statement):
    assert proving.negates(statement)


# ------------------------------------------------- 3. the outcome it produces
def _run_with(trace):
    run_ = ProofRun(goal="q", statement=GOAL, statement_ok=True)
    run_.trace.extend(trace)
    return run_


def test_a_verified_refutation_classifies_as_refuted():
    assert classify(_run_with(["refuted statement: Ω is not connected"])) is (
        ProofOutcome.REFUTED
    )


def test_an_unverified_report_stays_suspect():
    assert classify(_run_with(["suspect statement: Ω is not connected"])) is (
        ProofOutcome.SUSPECT_STATEMENT
    )


def test_a_normal_failure_is_still_not_proved():
    """The new outcome must not swallow the ordinary case."""
    assert classify(_run_with([])) is ProofOutcome.NOT_PROVED


# --------------------------------------------- 4. end to end, through `finish`
def runtime_for(workdir):
    from langchain.tools import ToolRuntime

    from math_v2.context import MathContext

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


def _attempted_and_failed(workdir):
    """One rejected proof attempt — what `suspect_refusal` requires before the
    suspect exit is allowed at all."""
    log.append(workdir, log.Record(kind=log.PROOF, statement=GOAL,
                                   proof="by simp", status=log.FALSE,
                                   detail="unsolved goals"))


def test_finish_upgrades_a_suspect_report_when_the_negation_compiled(workdir):
    from math_v2.tools.control import finish

    _attempted_and_failed(workdir)
    run(proving.try_refutation(
        workdir, NEGATION, "by simp", compiler(LeanOutcome.COMPILED)[0]))

    report = run(finish.ainvoke({"summary": "Ω is not assumed connected",
                                 "outcome": "statement_suspect",
                                 "runtime": runtime_for(workdir)}))

    assert report["accepted"] is True
    assert report["outcome"] == verdict.REFUTED
    assert report["evidence"]["refutation"]["statement"] == NEGATION


def test_finish_leaves_an_unverified_report_as_a_diagnostic(workdir):
    """The agent tried the counterexample and it was rejected. The report is
    allowed — trying is the requirement — but it stays a diagnostic."""
    from math_v2.tools.control import finish

    _attempted_and_failed(workdir)
    run(proving.try_refutation(
        workdir, NEGATION, "by simp", compiler(LeanOutcome.ERRORS, "error")[0]))

    report = run(finish.ainvoke({"summary": "Ω is not assumed connected",
                                 "outcome": "statement_suspect",
                                 "runtime": runtime_for(workdir)}))

    assert report["outcome"] == verdict.STATEMENT_SUSPECT
    assert "refutation" not in report.get("evidence", {})


def test_a_failed_refutation_does_not_upgrade_the_report(workdir):
    """The whole point: trying and failing must land exactly where not trying
    lands, or the attempt itself becomes the loophole."""
    from math_v2.tools.control import finish

    _attempted_and_failed(workdir)
    run(proving.try_refutation(
        workdir, NEGATION, "by simp", compiler(LeanOutcome.ERRORS, "error")[0]))

    report = run(finish.ainvoke({"summary": "looks false",
                                 "outcome": "statement_suspect",
                                 "runtime": runtime_for(workdir)}))

    assert report["outcome"] == verdict.STATEMENT_SUSPECT


def test_a_proof_outranks_a_refutation_note():
    """PROVED is a compiler fact and stays first. If both are somehow recorded
    the goal was proved, and the note is the thing that is wrong."""
    from domain.verdict import Verdict, VerificationStatus

    run_ = ProofRun(goal="q", statement=GOAL, statement_ok=True)
    run_.proof = "by norm_num"
    run_.verdict = Verdict(VerificationStatus.TRUE, "lean", "accepted")
    run_.trace.append("refuted statement: should not outrank a proof")

    assert run_.proved
    assert classify(run_) is ProofOutcome.PROVED
