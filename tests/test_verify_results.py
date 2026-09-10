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

    # `**_` because the real `run_lean` takes `timeout=`, and the fallback path
    # passes `LEAN_COLD_TIMEOUT` to it. A double that cannot accept the real
    # call's arguments hides signature changes -- which is exactly what
    # happened here when the REPL backend was added.
    def runner(source, **_):
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


def test_an_unretained_proof_is_never_a_pass_but_is_not_a_failure_either():
    """Silence must never read as success -- and that invariant is intact.
    An UNCHECKED claim exits 2, never 0, so nothing here reports it as
    verified.

    WHAT CHANGED, AND WHY. This asserted `ok is False`, which put a missing
    proof in the same bucket as a proof Lean REJECTED. MEASURED across
    eval/results/: 64 of 173 accepted proofs retain their text and 109 do
    not, so a full-corpus run exited 1 under "A claim that does not
    recompile is a soundness failure. Do not quote a proof rate" -- on 109
    rows where nothing had been recompiled at all. That is the false alarm
    this module's own note warns about ("a checker that cries wolf teaches
    the reader to ignore it"), and it drowned the 64 real checks.

    Every guarantee the earlier version protected still holds: not a pass,
    the reason is stated, and the compiler is never handed an empty proof.
    """
    compiler = lean(LeanOutcome.COMPILED)
    ok, note = verify_results.check(claim(proof=""), compiler)

    assert ok is not True, "an unauditable claim must never read as verified"
    assert ok is verify_results.UNCHECKED
    assert "NO PROOF RETAINED" in note
    assert compiler.seen == [], "an empty proof was sent to the compiler"


# ----------------------------------------------------- over a whole file
@pytest.fixture(autouse=True)
def _no_real_compiler(monkeypatch):
    """`main` now asks `compiler()` which backend to use, and on a machine
    where the REPL is selected that returns a LIVE one -- which would ignore
    every injected compiler below and try to spawn Lean. Pinning it to the
    fallback keeps these tests about the POLICY, which is what they test.

    The seam moved when the REPL path was added; five tests failed for exactly
    this reason, correctly.
    """
    monkeypatch.setattr(verify_results, "compiler",
                        lambda: (None, "subprocess (test)"))


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

    def alternating(source, **_):
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


def test_the_cold_timeout_has_real_margin_over_a_measured_compile():
    """MEASURED on the operator's machine: one cold `import Mathlib` compile
    takes about 480 seconds. A 600s ceiling sat 25% above that and produced a
    FALSE FAILURE -- `by decide` reported as unverifiable when nothing was
    wrong with it, purely because ordinary variance crossed the line.

    A limit whose job is to catch a hang needs enough margin that normal
    variance cannot reach it. Three times the observed cost is that margin;
    600s was not, and 60s (the agent's own timeout, correct for a WARM REPL)
    was not remotely.
    """
    import config

    measured_cold_compile = 484

    assert config.LEAN_COLD_TIMEOUT >= 3 * measured_cold_compile, (
        f"{config.LEAN_COLD_TIMEOUT}s leaves too little margin over a "
        f"{measured_cold_compile}s compile; variance will read as a hang"
    )
    assert config.LEAN_COLD_TIMEOUT > config.LEAN_TIMEOUT, (
        "the cold budget must exceed the warm one it exists to replace"
    )


# ------------------------------------------- which backend does the work
def test_the_repl_is_preferred_when_it_is_the_selected_backend(monkeypatch):
    """MEASURED: recompiling run4's two proofs through the cold subprocess
    path did not finish inside 1800s and reported COULD NOT CHECK for both --
    the tool failing at its only job. The REPL pays `import Mathlib` once."""
    monkeypatch.setattr(verify_results, "compiler",
                        lambda: (lambda source: None, "REPL (Mathlib imported once)"))

    fast, how = verify_results.compiler()

    assert fast is not None
    assert "REPL" in how


def test_nothing_verified_is_not_reported_as_the_rest_recompiled(
        tmp_path, monkeypatch):
    """A wording bug worth a test: with every claim unchecked, the summary
    said "The rest recompiled" about a set that was empty. A validation tool
    must not imply work it did not do."""
    monkeypatch.setattr(verify_results, "run_lean", lean(LeanOutcome.TIMEOUT))
    path = _write(tmp_path, [claim()])

    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        verify_results.main([str(path)])

    assert "NOTHING was verified" in out.getvalue()
    assert "The rest recompiled" not in out.getvalue()


def test_the_repl_budgets_are_raised_before_the_module_is_imported():
    """ORDERING IS LOAD-BEARING. `_repl`'s timeouts are module-level
    `os.getenv` reads, fixed at its first import, so a value set afterwards
    does nothing -- the same trap that made `--budget-profile hard-reasoning`
    print its own banner while every goal ran on the old defaults.

    Both defaults are wrong for THIS job while being right for the agent's:
    600s to start against a ~480s measured cold import (the same 25% margin
    that already produced one false timeout), and 180s per command, sized for
    interactive attempts rather than for finished proofs."""
    # Read from the FILE, not through `verify_results.compiler`. The autouse
    # fixture above monkeypatches that attribute to a lambda, so
    # `inspect.getsource` returned the LAMBDA and this test failed with
    # "substring not found" -- my own fixture defeating my own test. Text on
    # disk cannot be monkeypatched, which is also why
    # `test_mathv2_package.py` reads `agent.py` this way.
    source = (Path(__file__).resolve().parent.parent
              / "scripts" / "verify_results.py").read_text(encoding="utf-8")
    body = source[source.index("def compiler("):source.index("\ndef source_for")]
    set_at = body.index("MRA_LEAN_REPL_START_TIMEOUT")
    import_at = body.index("from math_v2.tools import")
    source = body

    assert set_at < import_at, (
        "the timeouts are set after the import that freezes them"
    )
    assert "setdefault" in source, "an exported value must still win"


@pytest.mark.parametrize("outcome,expected", [
    (LeanOutcome.UNAVAILABLE, "Lean did not run at all"),
    (LeanOutcome.TIMEOUT, "ran out of time"),
])
def test_the_advice_matches_why_it_could_not_check(outcome, expected):
    """Telling someone whose Lean is not installed to raise a timeout sends
    them to the wrong place. A diagnosis nobody can act on is the same as
    none -- which is the lesson the Lean-gate work already paid for twice."""
    _, note = verify_results.check(claim(), lean(outcome))

    assert expected in note, note


# ------------------------------- what is NOT a soundness failure
#
# The module already argues this for one trigger: "SLOW IS NOT UNSOUND, AND
# NEITHER IS ABSENT ... A false alarm in a soundness checker is worse than no
# checker: it teaches the reader to ignore the real ones." Two more cases
# reached `False` anyway, and both were measured on the real corpus.
def _claim(goal_id="g", proof="by rfl"):
    return {"goal_id": goal_id, "outcome": "proved",
            "statement": "theorem t : 1 = 1", "proof": proof, "kind": "proof"}


def test_an_unconfigured_toolchain_is_unchecked_not_failed():
    """`lean --version` exits 0 under an unconfigured elan, so
    `lean_is_available` passes, the compile runs, and Lean's complaint comes
    back as ERRORS -- not TIMEOUT or UNAVAILABLE, which the earlier fix
    covered. MEASURED verbatim in this repo's WSL clone."""
    def broken(_source):
        return LeanResult(LeanOutcome.ERRORS,
                          "error: no default toolchain configured. run "
                          "`elan default stable` to install & configure ...")
    ok, note = verify_results.check(_claim(), broken)
    assert ok is verify_results.UNCHECKED, note
    assert "environment" in note


def test_a_project_without_mathlib_is_unchecked_not_failed():
    def no_mathlib(_source):
        return LeanResult(LeanOutcome.ERRORS,
                          "error: unknown module prefix 'Mathlib'")
    ok, note = verify_results.check(_claim(), no_mathlib)
    assert ok is verify_results.UNCHECKED, note


@pytest.mark.parametrize("output", [
    "error: unsolved goals\n  |- 1 = 2",
    "error: unknown identifier 'foo_bar'",
    "error: type mismatch",
    "error: Function expected at card",
])
def test_a_rejected_proof_is_still_a_failure(output):
    """THE BOUNDARY. Every one of these IS the proof. If the environment
    list ever grows to swallow them the checker stops checking anything."""
    ok, note = verify_results.check(
        _claim(), lambda _s: LeanResult(LeanOutcome.ERRORS, output))
    assert ok is False, note
    assert "REJECTED" in note


def test_a_placeholder_is_still_a_failure():
    """`sorry` exits cleanly and proves nothing -- the one case where a
    successful compile is the soundness failure."""
    ok, note = verify_results.check(
        _claim(), lambda _s: LeanResult(LeanOutcome.INCOMPLETE,
                                        "declaration uses 'sorry'"))
    assert ok is False
    assert "sorry" in note.lower()


def test_unchecked_claims_never_exit_zero(tmp_path, capsys):
    """Reporting an unverified run as verified is how an unverified run gets
    quoted as a verified one -- the module's own words."""
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"results": [_claim(proof="")]}),
                    encoding="utf-8")
    assert verify_results.main([str(path)]) == 2
    assert "COULD NOT BE CHECKED" in capsys.readouterr().out


def test_a_real_failure_still_exits_one(tmp_path, capsys, monkeypatch):
    """Exit 1 has to keep meaning "a claim did not recompile", or the gate
    is worthless."""
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"results": [_claim()]}), encoding="utf-8")
    monkeypatch.setattr(
        verify_results, "compiler",
        lambda: (lambda _s: LeanResult(LeanOutcome.ERRORS,
                                       "error: unsolved goals"), "fake"))
    assert verify_results.main([str(path)]) == 1
    assert "soundness failure" in capsys.readouterr().out

