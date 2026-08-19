"""Tool bodies, offline. No container, no model, no network, no Lean.

The Lean seam is injected, so what runs here is exactly the code that will run
under a CommandSpec — only the dispatcher differs.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from math_v2.core import log, proving, retrieval, symbolic, verdict
from retrieval.loogle import Premise
from verifiers.lean_runner import LeanOutcome, LeanResult

SCRIPTS = Path(__file__).resolve().parent.parent / "subagents" / "math" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import math_worker  # noqa: E402

STATEMENT = "theorem mra_goal : 2 + 2 = 4"


@pytest.fixture
def workdir(tmp_path):
    return str(tmp_path)


def lean(outcome, output=""):
    """An injected compiler that always answers the same way."""
    seen = []

    async def run(source):
        seen.append(source)
        return LeanResult(outcome, output)

    run.seen = seen
    return run


def run(coro):
    return asyncio.run(coro)


class Search:
    def __init__(self, found=(), suggestions=()):
        self.found = list(found)
        self.suggestions = list(suggestions)
        self.queries = []

    def search_with_suggestions(self, query, limit=None):
        self.queries.append(query)
        return list(self.found), list(self.suggestions)


# ----------------------------------------------------------------- try_proof
def test_an_accepted_proof_is_recorded_and_proves_the_goal(workdir):
    result = run(proving.try_proof(workdir, STATEMENT, "by norm_num",
                                   lean(LeanOutcome.COMPILED)))

    assert result["outputs"]["accepted"] is True
    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.PROVED


def test_a_rejected_proof_returns_the_goal_state(workdir):
    compiler = lean(LeanOutcome.ERRORS,
                    "f.lean:4:2: error: unsolved goals\n⊢ IsCyclic G")
    result = run(proving.try_proof(workdir, STATEMENT, "exact wrong", compiler))

    assert result["outputs"]["accepted"] is False
    assert "⊢ IsCyclic G" in result["message"], "the goal state never reached the model"
    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.NOT_PROVED


def test_the_model_never_writes_the_lean_file(workdir):
    """qe_v2's rule: the file is rendered, and the goal is renamed for us."""
    compiler = lean(LeanOutcome.COMPILED)
    run(proving.try_proof(workdir,
                          "theorem irrational_sqrt_two : Irrational (Real.sqrt 2)",
                          "exact irrational_sqrt_two", compiler))

    source = compiler.seen[0]
    assert "import Mathlib" in source
    assert "theorem mra_goal" in source, "the goal was not renamed off the library name"
    assert "exact irrational_sqrt_two" in source, "the proof was rewritten"


# ---------------------------------------------------------------- try_lemma
def test_a_kept_lemma_is_cited_by_later_attempts(workdir):
    compiler = lean(LeanOutcome.COMPILED)
    run(proving.try_lemma(workdir, "lemma helper : True", "trivial", compiler))
    run(proving.try_proof(workdir, STATEMENT, "exact helper", compiler))

    assert "lemma helper" in compiler.seen[-1], "the goal compiled without the lemma"
    assert "theorem mra_goal" in compiler.seen[-1]


def test_proving_a_lemma_does_not_prove_the_goal(workdir):
    """THE constraint, at the level the tool body owns."""
    run(proving.try_lemma(workdir, "lemma helper : True", "trivial",
                          lean(LeanOutcome.COMPILED)))

    assert log.kept_lemmas(workdir)
    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.NOT_PROVED


def test_a_rejected_lemma_is_not_kept(workdir):
    result = run(proving.try_lemma(workdir, "lemma bad : False", "trivial",
                                   lean(LeanOutcome.ERRORS, "error: no")))
    assert result["outputs"]["accepted"] is False
    assert log.kept_lemmas(workdir) == []


def test_kept_lemmas_are_bounded(workdir):
    compiler = lean(LeanOutcome.COMPILED)
    for index in range(5):
        run(proving.try_lemma(workdir, f"lemma h{index} : True", "trivial",
                              compiler, limit=2))
    assert len(log.kept_lemmas(workdir)) == 2


# -------------------------------------------------------------- try_skeleton
def test_a_typechecking_skeleton_lists_what_is_left_and_proves_nothing(workdir):
    compiler = lean(LeanOutcome.INCOMPLETE)
    result = run(proving.try_skeleton(
        workdir, STATEMENT,
        "have h1 : 1 = 1 := by sorry\nhave h2 : 2 = 2 := by sorry\nexact h1",
        compiler,
    ))

    assert result["outputs"]["typechecks"] is True
    assert "1 = 1" in result["message"] and "2 = 2" in result["message"]
    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.NOT_PROVED


def test_a_skeleton_that_does_not_typecheck_says_so(workdir):
    result = run(proving.try_skeleton(workdir, STATEMENT, "have h : True := by sorry",
                                      lean(LeanOutcome.ERRORS, "error: bad")))
    assert result["outputs"]["typechecks"] is False


# ------------------------------------------------------------ check_statement
def test_a_signature_that_elaborates_passes(workdir):
    result = run(proving.check_statement(workdir, STATEMENT, lean(LeanOutcome.INCOMPLETE)))
    assert result["outputs"]["elaborates"] is True


def test_a_signature_lean_cannot_parse_is_a_formalisation_failure(workdir):
    compiler = lean(LeanOutcome.ERRORS, "f.lean:3:1: error: unknown identifier 'Basis'")
    result = run(proving.check_statement(workdir, "theorem t : Basis", compiler))

    assert result["outputs"]["elaborates"] is False
    assert verdict.proof_verdict(workdir, "theorem t : Basis")["outcome"] == (
        verdict.NOT_FORMALIZED
    )


def test_the_statement_is_checked_with_sorry(workdir):
    compiler = lean(LeanOutcome.INCOMPLETE)
    run(proving.check_statement(workdir, STATEMENT, compiler))
    assert "sorry" in compiler.seen[0]


# ------------------------------------------------------- standard tactics
def test_standard_tactics_use_every_premise_found_so_far(workdir):
    search = Search(found=[Premise(name="isCyclic_of_prime_card", type=" : IsCyclic α")])
    retrieval.search_mathlib(workdir, "IsCyclic", search)

    compiler = lean(LeanOutcome.ERRORS, "error: no")
    run(proving.try_standard_tactics(workdir, STATEMENT, compiler))

    assert "isCyclic_of_prime_card" in compiler.seen[0]


# ------------------------------------------------------------------ retrieval
def test_a_search_records_the_names_it_found(workdir):
    search = Search(found=[Premise(name="Nat.exists_infinite_primes", type=" : ...")])
    result = retrieval.search_mathlib(workdir, "primes", search)

    assert result["outputs"]["found"][0]["name"] == "Nat.exists_infinite_primes"
    assert "Nat.exists_infinite_primes" in log.read(workdir)["trace"][0]


def test_loogles_suggestions_are_passed_on_when_nothing_matched(workdir):
    search = Search(found=[], suggestions=["Module.Basis"])
    result = retrieval.search_mathlib(workdir, "Basis", search)

    assert "Module.Basis" in result["message"]


def test_search_being_unavailable_is_not_an_error(workdir):
    result = retrieval.search_mathlib(workdir, "anything", None)
    assert result["ok"] is True


# ------------------------------------------------------------------ symbolic
async def real_dispatch(op, args):
    """The actual worker, in process — no container needed to test the wiring."""
    return math_worker.run_op(op, args)


def test_a_computation_is_recorded_and_reported(workdir):
    result = run(symbolic.compute(workdir, "check_primality", {"lhs": "561"},
                                  real_dispatch))

    assert result["outputs"]["status"] == "false"
    assert "FALSE" in result["message"]
    assert log.records(workdir, "computation")


def test_a_refutation_is_not_presented_as_a_tool_failure(workdir):
    result = run(symbolic.compute(workdir, "check_primality", {"lhs": "561"},
                                  real_dispatch))
    assert result["ok"] is True
    assert "not a failure" in result["message"]


def test_a_computation_can_never_establish_a_proof(workdir):
    run(symbolic.compute(workdir, "check_numeric", {"lhs": "2+2", "rhs": "4"},
                         real_dispatch))

    decision = verdict.proof_verdict(workdir, STATEMENT)
    assert decision["outcome"] == verdict.NOT_PROVED, "a computation was read as a proof"
    assert verdict.refuse(verdict.PROVED, decision)


def test_an_unknown_operation_is_refused(workdir):
    result = run(symbolic.compute(workdir, "check_vibes", {}, real_dispatch))
    assert result["ok"] is False


def test_the_tool_layer_and_the_worker_agree_on_every_op():
    """Two registries that disagree would fail only at runtime, in the SIF."""
    assert set(symbolic.OPS) == set(math_worker.OPS)
    for op, fields in symbolic.OPS.items():
        assert set(fields) == set(math_worker.OPS[op][1]), op


def test_arguments_the_op_does_not_take_are_dropped_before_dispatch(workdir):
    """The worker refuses unexpected arguments; the tool layer must not send them."""
    seen = {}

    async def dispatch(op, args):
        seen.update(args)
        return {"ok": True, "outputs": {"status": "true", "detail": ""}}

    run(symbolic.compute(workdir, "check_primality",
                         {"lhs": "7", "relation": "<"}, dispatch))
    assert seen == {"lhs": "7"}
