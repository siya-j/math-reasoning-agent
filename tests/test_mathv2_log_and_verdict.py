"""The guard, migrated. These are the tests that must never be deleted.

`test_prose_cannot_produce_a_proof` and `test_a_lemma_is_not_the_goal` carry
the whole guarantee across the move to the blueprint's shape. If the migration
broke anything, it broke it here.
"""

import json
import os

import pytest

from math_v2.core import log, verdict

STATEMENT = "theorem mra_goal : 2 + 2 = 4"


@pytest.fixture
def workdir(tmp_path):
    return str(tmp_path)


def record(**kwargs):
    return log.Record(**kwargs)


# ------------------------------------------------------------------ the guard
def test_prose_cannot_produce_a_proof(workdir):
    """Nothing ran. There is nothing to find, whatever the agent says."""
    result = verdict.proof_verdict(workdir, STATEMENT)

    assert result["outcome"] == verdict.NOT_PROVED
    assert verdict.refuse(verdict.PROVED, result), "an empty log allowed a proof claim"


def test_a_recorded_acceptance_is_what_proves_it(workdir):
    log.append(workdir, record(kind=log.PROOF, statement=STATEMENT,
                               proof="by norm_num", status=log.TRUE))

    result = verdict.proof_verdict(workdir, STATEMENT)
    assert result["outcome"] == verdict.PROVED
    assert verdict.refuse(verdict.PROVED, result) == ""


def test_a_rejected_attempt_proves_nothing(workdir):
    log.append(workdir, record(kind=log.PROOF, statement=STATEMENT,
                               proof="by sorry", status=log.UNKNOWN))
    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.NOT_PROVED


def test_a_lemma_is_not_the_goal(workdir):
    """A run that proves five helpers and closes nothing has proved nothing."""
    for index in range(5):
        log.append(workdir, record(kind=log.LEMMA, statement=f"lemma h{index} : True",
                                   proof="trivial", status=log.TRUE))
        log.keep_lemma(workdir, f"lemma h{index} : True := trivial")

    result = verdict.proof_verdict(workdir, STATEMENT)
    assert result["outcome"] == verdict.NOT_PROVED, "a helper was read as the goal"
    assert "5 helper lemma(s)" in result["reason"], "the work done should still show"


def test_a_typechecking_skeleton_is_not_a_proof(workdir):
    """It compiles via `sorry`, and `sorry` proves nothing."""
    log.append(workdir, record(kind=log.SKELETON, statement=STATEMENT,
                               proof="have h : True := by sorry", status=log.TRUE))
    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.NOT_PROVED


def test_a_proof_of_a_DIFFERENT_statement_is_not_accepted(workdir):
    """One conversation may cover several claims."""
    log.append(workdir, record(kind=log.PROOF, statement="theorem other : True",
                               proof="trivial", status=log.TRUE))

    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.NOT_PROVED
    assert verdict.proof_verdict(workdir, "theorem other : True")["outcome"] == (
        verdict.PROVED
    )


def test_symbolic_computation_can_never_establish_a_proof(workdir):
    """SymPy informs the proving path. It does not decide it."""
    log.append(workdir, record(kind=log.PROOF, statement=STATEMENT,
                               proof="by norm_num", status=log.TRUE))
    result = verdict.proof_verdict(workdir, STATEMENT)

    assert verdict.refuse(verdict.VERIFIED_TRUE, result)
    assert verdict.refuse(verdict.VERIFIED_FALSE, result)


def test_a_failed_proof_is_never_reported_as_false(workdir):
    """Lean failing to prove P is not evidence against P."""
    log.append(workdir, record(kind=log.PROOF, statement=STATEMENT,
                               proof="nonsense", status=log.UNKNOWN))
    result = verdict.proof_verdict(workdir, STATEMENT)

    assert result["outcome"] != verdict.VERIFIED_FALSE
    assert "not evidence that the claim is false" in result["reason"]


# ------------------------------------------------------- formalisation split
def test_an_unelaborable_statement_is_a_formalisation_failure(workdir):
    log.append(workdir, record(kind=log.STATEMENT_CHECK, statement=STATEMENT,
                               status=log.FALSE, detail="unknown identifier 'Basis'"))

    result = verdict.proof_verdict(workdir, STATEMENT)
    assert result["outcome"] == verdict.NOT_FORMALIZED


def test_a_statement_that_elaborated_does_not_mask_a_proving_failure(workdir):
    log.append(workdir, record(kind=log.STATEMENT_CHECK, statement=STATEMENT,
                               status=log.TRUE))
    log.append(workdir, record(kind=log.PROOF, statement=STATEMENT,
                               proof="nope", status=log.UNKNOWN))

    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.NOT_PROVED


# ------------------------------------------------------------------- the file
def test_the_log_survives_a_process_boundary(workdir):
    """It must outlive the turn, and history compaction."""
    log.append(workdir, record(kind=log.PROOF, statement=STATEMENT, status=log.TRUE))

    reread = log.read(workdir)
    assert len(reread["records"]) == 1
    assert os.path.exists(log.log_path(workdir))


def test_a_corrupt_log_reads_as_empty_rather_than_crashing(workdir):
    path = log.log_path(workdir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ this is not json")

    assert log.read(workdir)["records"] == []
    assert verdict.proof_verdict(workdir, STATEMENT)["outcome"] == verdict.NOT_PROVED


def test_a_log_missing_its_keys_does_not_break_the_guard(workdir):
    path = log.log_path(workdir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"records": "not a list"}, handle)

    assert log.read(workdir)["records"] == []


def test_lemmas_are_kept_without_duplicates(workdir):
    log.keep_lemma(workdir, "lemma h : True := trivial")
    log.keep_lemma(workdir, "lemma h : True := trivial")
    assert log.kept_lemmas(workdir) == ["lemma h : True := trivial"]


def test_clearing_is_explicit(workdir):
    log.append(workdir, record(kind=log.PROOF, statement=STATEMENT, status=log.TRUE))
    log.clear(workdir)
    assert log.read(workdir)["records"] == []


# ------------------------------------------------------------ gotcha 1 guard
def test_no_core_module_stringifies_its_annotations():
    """`from __future__ import annotations` breaks ToolRuntime injection.

    Blueprint §5.1 and gotcha 1: it applies to tool modules AND the helpers
    they import, which is every module in core/.
    """
    import pathlib
    import re

    # A real import line, not the prose in a docstring explaining the rule —
    # which is what the first version of this test matched.
    statement = re.compile(r"^\s*from __future__ import\b", re.MULTILINE)

    core = pathlib.Path(__file__).resolve().parents[1] / "math_v2" / "core"
    offenders = [
        path.name
        for path in core.glob("*.py")
        if statement.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"tool helpers stringify annotations: {offenders}"
