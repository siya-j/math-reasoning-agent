"""Independent re-verification: recompile a claimed proof and check it stands.

WHY THIS EXISTS
---------------
The system asserts exactly one thing -- "the compiler accepted this" -- and
until now nothing ever went back and checked a reported proof against a
compiler a second time. The claim was audited on the way out and never again.
`math_v2.harness._to_proof_run` re-derives the verdict so "a harness bug
cannot promote a claim the guard would have refused", but a bug in the
RECORDING path is invisible to both the guard and that re-derivation, because
both read the same records.

MEASURED, and the reason this could not have existed before: the results file
did not retain enough to try. `ProofResult` stored each attempt truncated to
600 characters, so `putnam_1962_b1` -- the one PROVED result across two
PutnamBench runs -- was cut off mid-`have`, and the kept lemmas its proof
cites by name were not stored at all. Running the new script against that
file reports NO PROOF RETAINED, which is the honest answer and was the
motivating finding.

Every test here injects a fake compiler. The policy being tested is a policy
about MEANING -- what counts as a verified claim -- and meaning must not
require a 3 GB toolchain to test, the same reasoning `verifiers/
lean_verifier.py` gives for injecting its runner.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import verify_results  # noqa: E402
from domain.proof import Lemma, ProofRun, Verdict, VerificationStatus  # noqa: E402
from eval.proof_dataset import Goal, Tier  # noqa: E402
from eval.proof_metrics import result_from  # noqa: E402
from verifiers.lean_runner import LeanOutcome, LeanResult  # noqa: E402

STATEMENT = "theorem putnam_x (n : Nat) : n + 0 = n"
PROOF = "by simp"


def lean(outcome, output=""):
    seen = []

    def runner(source):
        seen.append(source)
        return LeanResult(outcome, output)

    runner.seen = seen
    return runner


def claim(**overrides):
    base = {"goal_id": "g1", "outcome": "proved", "statement": STATEMENT,
            "proof": PROOF, "lemmas": []}
    return base | overrides


# ------------------------------------------------- rebuilding the artefact
def test_kept_lemmas_come_first_so_only_the_goal_is_renamed():
    """Not cosmetic. `rename_goal` renames the LAST declaration, which is what
    leaves the kept lemmas under the names the accepted proof cites. Lemmas
    after the goal would rename a lemma and orphan every citation."""
    source = verify_results.source_for(claim(
        lemmas=["lemma helper (n : Nat) : n + 0 = n := by simp"]))

    assert source.index("lemma helper") < source.index("mra_goal")
    assert "theorem mra_goal" in source


def test_a_claim_with_no_lemmas_still_builds():
    source = verify_results.source_for(claim())

    assert "theorem mra_goal" in source
    assert PROOF in source


def test_the_proof_is_carried_through_verbatim():
    """A re-verifier that rewrote the proof would be checking its own work."""
    proof = "by\n  induction n with\n  | zero => simp\n  | succ k ih => omega"

    assert proof in verify_results.source_for(claim(proof=proof))


# --------------------------------------------------------- the verdicts
def test_a_recompiling_proof_passes():
    ok, note = verify_results.check(claim(), lean(LeanOutcome.COMPILED))

    assert ok is True
    assert note == "recompiled"


def test_a_proof_that_only_compiles_because_of_sorry_is_a_failure():
    """THE most important case this script can catch. Lean exits CLEANLY on
    `sorry` -- that is exactly why it is dangerous, and why INCOMPLETE is
    treated as a hard failure here rather than as a pass or a warning. A
    "proof" that compiles and means nothing is the one way a false claim can
    survive every check upstream of this one."""
    ok, note = verify_results.check(claim(), lean(LeanOutcome.INCOMPLETE))

    assert ok is False
    assert "sorry" in note
    assert "soundness failure" in note


def test_a_rejected_proof_reports_what_lean_said():
    ok, note = verify_results.check(
        claim(), lean(LeanOutcome.ERRORS, "error: unknown identifier 'foo'"))

    assert ok is False
    assert "unknown identifier 'foo'" in note


def test_an_unretained_proof_is_a_failure_not_a_pass():
    """Silence must never read as success. A results file that predates proof
    retention cannot support its own claim, and saying so is the honest
    answer -- MEASURED: this is exactly what putnam-run2.json reports."""
    compiler = lean(LeanOutcome.COMPILED)
    ok, note = verify_results.check(claim(proof=""), compiler)

    assert ok is False
    assert "NO PROOF RETAINED" in note
    assert compiler.seen == [], "an empty proof was sent to the compiler"


# ----------------------------------------------------- over a whole file
def _write(tmp_path, results):
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"results": results}), encoding="utf-8")
    return path


def test_only_proved_results_are_checked(tmp_path):
    """A `not_proved` goal makes no claim, so there is nothing to verify and
    counting it would dilute the one number that matters."""
    path = _write(tmp_path, [claim(), claim(goal_id="g2", outcome="not_proved"),
                             claim(goal_id="g3", outcome="exhausted")])

    checked, failures, unchecked = verify_results.verify(
        path, lean(LeanOutcome.COMPILED))

    assert checked == 1
    assert failures == []
    assert unchecked == []


def test_a_failure_is_reported_with_its_goal(tmp_path):
    path = _write(tmp_path, [claim(), claim(goal_id="bad")])

    checked, failures, _ = verify_results.verify(
        path, lean(LeanOutcome.INCOMPLETE))

    assert checked == 2
    assert [goal for goal, _ in failures] == ["g1", "bad"]


# --------------------------------------------------------- the exit code
def test_exit_zero_when_every_claim_recompiles(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_results, "run_lean", lean(LeanOutcome.COMPILED))
    path = _write(tmp_path, [claim()])

    assert verify_results.main([str(path)]) == 0


def test_exit_one_on_a_soundness_failure(tmp_path, monkeypatch):
    """1 is the loudest signal this repo can produce, and it must be an exit
    code rather than a printed warning so a script or a human running this
    before quoting a proof rate cannot miss it."""
    monkeypatch.setattr(verify_results, "run_lean", lean(LeanOutcome.INCOMPLETE))
    path = _write(tmp_path, [claim()])

    assert verify_results.main([str(path)]) == 1


def test_exit_two_when_there_was_nothing_to_check(tmp_path, monkeypatch):
    """Distinct from 0 on purpose. "Nothing was checked" and "everything
    passed" must never be the same exit code -- that conflation is how an
    unverified run gets quoted as a verified one."""
    monkeypatch.setattr(verify_results, "run_lean", lean(LeanOutcome.COMPILED))
    path = _write(tmp_path, [claim(outcome="not_proved")])

    assert verify_results.main([str(path)]) == 2


def test_exit_two_when_no_file_matches():
    assert verify_results.main(["nonexistent-*.json"]) == 2


# ------------------------------------- the evidence reaches the file at all
def test_the_results_record_keeps_the_full_proof_untruncated():
    """MEASURED: `stages` caps a proof at 600 characters, which lost the only
    PROVED result this project had. The evidence for a claim must survive in
    full or the claim cannot be rechecked."""
    long_proof = "by\n" + "\n".join(f"  have h{i} : True := trivial"
                                    for i in range(80))
    assert len(long_proof) > 600

    run = ProofRun(goal="g")
    run.statement = STATEMENT
    run.proof = long_proof
    run.verdict = Verdict(VerificationStatus.TRUE, "lean", "")

    record = result_from(Goal(id="g1", goal="g", area="a",
                              tier=Tier.IN_MATHLIB), run)

    assert record.proof == long_proof


def test_the_results_record_keeps_the_lemmas_the_proof_cites():
    """Not optional detail: a kept lemma is prepended to the goal before
    compiling and the accepted proof cites it BY NAME, so a record without
    them would make re-verification report a false failure."""
    run = ProofRun(goal="g")
    run.statement = STATEMENT
    run.proof = PROOF
    run.verdict = Verdict(VerificationStatus.TRUE, "lean", "")
    run.lemmas = [Lemma(informal="", statement="lemma helper : True",
                        proof="lemma helper : True := trivial",
                        verdict=Verdict(VerificationStatus.TRUE, "lean", "kept"))]

    record = result_from(Goal(id="g1", goal="g", area="a",
                              tier=Tier.IN_MATHLIB), run)

    assert record.lemmas == ("lemma helper : True := trivial",)


# ------------------------------------- slow, absent, and unsound are three
# MEASURED, on a machine where Lean 4.33.1, the Lake project and Mathlib were
# all healthy: `import Mathlib` cold TIMED OUT at the default 60s, and this
# script reported the run's one genuine proof as a soundness failure telling
# the reader not to quote their proof rate. A checker that cries wolf is worse
# than no checker.
@pytest.mark.parametrize("outcome", [LeanOutcome.TIMEOUT,
                                     LeanOutcome.UNAVAILABLE])
def test_a_timeout_or_missing_lean_is_not_a_soundness_failure(outcome):
    ok, note = verify_results.check(claim(), lean(outcome))

    assert ok is verify_results.UNCHECKED, note
    assert "COULD NOT CHECK" in note
    assert "soundness" not in note.lower()


def test_an_unchecked_claim_exits_two_rather_than_zero(tmp_path, monkeypatch):
    """"Could not check" must not read as "verified". This is the same
    conflation the exit codes already separate for the empty case, one step
    further in."""
    monkeypatch.setattr(verify_results, "run_lean", lean(LeanOutcome.TIMEOUT))
    path = _write(tmp_path, [claim()])

    assert verify_results.main([str(path)]) == 2


def test_an_unchecked_claim_does_not_exit_one_either(tmp_path, monkeypatch):
    """Exit 1 means "a claimed proof did not recompile", which is a statement
    about the PROOF. A timeout is a statement about the machine."""
    monkeypatch.setattr(verify_results, "run_lean", lean(LeanOutcome.TIMEOUT))
    path = _write(tmp_path, [claim()])

    assert verify_results.main([str(path)]) != 1


def test_a_real_failure_still_outranks_an_unchecked_one(tmp_path, monkeypatch):
    """With both present, the soundness failure is the headline -- it must not
    be downgraded to "incomplete" by an unrelated timeout elsewhere."""
    calls = {"n": 0}

    def alternating(source):
        calls["n"] += 1
        outcome = (LeanOutcome.INCOMPLETE if calls["n"] == 1
                   else LeanOutcome.TIMEOUT)
        return LeanResult(outcome, "")

    monkeypatch.setattr(verify_results, "run_lean", alternating)
    path = _write(tmp_path, [claim(), claim(goal_id="g2")])

    assert verify_results.main([str(path)]) == 1


def test_the_unchecked_note_says_how_to_fix_it():
    """A diagnosis the reader cannot act on is a dead end -- the same reason
    the Mathlib skip reason names MRA_LEAN_PROJECT."""
    _, note = verify_results.check(claim(), lean(LeanOutcome.TIMEOUT))

    assert "MRA_LEAN_COLD_TIMEOUT" in note
    assert "diagnose_lean" in note
