"""The reporting layer must never be the thing that ends a run.

MEASURED FAILURE. `SUSPECT_STATEMENT` and `EXHAUSTED` were added to
`ProofOutcome` and the summary was updated to count them, but the per-goal
display map in `scripts/evaluate_proofs.py` was a dict literal indexed inline
and still covered three of six members. A live ProofNet run died with

    KeyError: <ProofOutcome.SUSPECT_STATEMENT: 'suspect_statement'>

on the FIRST goal, after the model call had already been paid for. Nothing was
wrong with the prover, the classification or the JSON — only the label.

These tests fix the general shape of that bug rather than the one instance:
adding a seventh outcome must fail here, loudly and offline, instead of on a
benchmark run an hour in.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.proof import ProofRun
from eval.proof_dataset import Goal, Tier
from eval.proof_metrics import ProofOutcome, ProofResult, render, result_from, summarize
from scripts.evaluate_proofs import MARKS, completed, mark_for, save

GOAL = Goal(id="exercise_1_13c", area="analysis", goal="q", tier=Tier.PROOFNET)


def result(outcome, goal_id="g"):
    return ProofResult(goal_id=goal_id, area="analysis", tier=Tier.PROOFNET,
                       outcome=outcome)


# ------------------------------------------------------- 1. the crash itself
def test_every_outcome_has_a_display_label():
    """THE regression. A seventh outcome fails here, not on a live run."""
    missing = [o.name for o in ProofOutcome if o not in MARKS]

    assert not missing, (
        f"{missing} would crash the per-goal line. Add them to MARKS in "
        "scripts/evaluate_proofs.py."
    )


def test_the_outcome_that_crashed_the_pilot_prints():
    assert mark_for(ProofOutcome.SUSPECT_STATEMENT).startswith("SUSPECT STATEMENT")


def test_a_verified_refutation_does_not_read_like_an_unverified_report():
    """They are different claims and the log must not blur them: one rests on a
    compilation, the other on the agent's prose."""
    assert MARKS[ProofOutcome.REFUTED] != MARKS[ProofOutcome.SUSPECT_STATEMENT]
    assert "unverified" in MARKS[ProofOutcome.SUSPECT_STATEMENT]


def test_a_label_lookup_never_raises():
    """Belt and braces. The test above keeps MARKS complete; this keeps a run
    alive if it ever is not, because aborting costs real model calls."""
    for outcome in ProofOutcome:
        assert mark_for(outcome)

    class Rogue:
        value = "something_new"

    assert mark_for(Rogue()) == "something new", "an unknown outcome aborted a run"


def test_the_labels_are_distinguishable():
    """Two outcomes printing the same string would hide the distinction the
    whole vocabulary exists to make."""
    assert len(set(MARKS.values())) == len(MARKS)


def test_a_suspect_statement_does_not_read_as_a_proving_failure():
    """`not proved` for a row we believe is false would be actively misleading
    to whoever reads the log."""
    assert MARKS[ProofOutcome.SUSPECT_STATEMENT] != MARKS[ProofOutcome.NOT_PROVED]
    assert MARKS[ProofOutcome.EXHAUSTED] != MARKS[ProofOutcome.NOT_PROVED]


# ------------------------------------------------ 2. it survives the round trip
def test_json_is_written_for_every_outcome(tmp_path):
    """Requirement 5. `save` runs after EVERY goal, so a member it cannot
    serialise loses the whole run, not one row."""
    out = tmp_path / "run.json"
    results = [result(o, goal_id=o.value) for o in ProofOutcome]

    save(results, summarize(results), out)
    written = json.loads(out.read_text())

    assert [r["outcome"] for r in written["results"]] == [o.value for o in ProofOutcome]


def test_a_saved_run_can_be_resumed(tmp_path):
    """`--resume` reconstructs ProofOutcome from the string. An unknown member
    is silently dropped, which would re-run goals that were already decided."""
    out = tmp_path / "run.json"
    results = [result(o, goal_id=o.value) for o in ProofOutcome]
    save(results, summarize(results), out)

    carried = completed(True, out)

    # ERROR is deliberately excluded — it was never decided.
    assert {r.outcome for r in carried} == set(ProofOutcome) - {ProofOutcome.ERROR}


def test_the_rendered_summary_names_every_category(tmp_path):
    """Requirement 3. All five must be visible and separate in the report."""
    text = render(summarize([result(o, goal_id=o.value) for o in ProofOutcome]))

    for line in ("proof rate", "statement not elaborable", "statement suspect",
                 "budget exhausted", "genuinely tested"):
        assert line in text, line


# ------------------------- 3. the denominator holds VALID PROOF TARGETS only
def test_a_suspect_statement_is_not_a_valid_proof_target():
    """A goal the agent reported broken is not one the prover was fairly asked
    to close, so it leaves the denominator — and `genuinely_tested` keeps
    counting it, because the work was done."""
    summary = summarize([result(ProofOutcome.PROVED, "a"),
                         result(ProofOutcome.SUSPECT_STATEMENT, "b")])

    assert summary["valid_proof_targets"] == 1
    assert summary["proof_rate_of_tested"] == 1.0
    assert summary["genuinely_tested"] == 2, "the diagnostic still counts it"
    assert summary["suspect_unverified"] == 1


def test_a_verified_refutation_is_excluded_too():
    """Same exclusion, different reason: Lean proved the negation, so no proof
    could have existed."""
    summary = summarize([result(ProofOutcome.PROVED, "a"),
                         result(ProofOutcome.REFUTED, "b")])

    assert summary["valid_proof_targets"] == 1
    assert summary["proof_rate_of_tested"] == 1.0
    assert summary["refuted"] == 1


def test_no_valid_target_reports_n_a_and_never_zero_percent():
    """THE case from the 4-goal run: 1 not_formalized, 3 suspect, 0 proved.

    0% would say the prover was handed valid goals and closed none of them.
    Nothing of the sort happened — it was handed none. The two readings lead
    to opposite decisions about what to work on next, so the report must not
    blur them.
    """
    summary = summarize([result(ProofOutcome.NOT_FORMALIZED, "a"),
                         result(ProofOutcome.SUSPECT_STATEMENT, "b"),
                         result(ProofOutcome.SUSPECT_STATEMENT, "c"),
                         result(ProofOutcome.SUSPECT_STATEMENT, "d")])

    assert summary["valid_proof_targets"] == 0
    assert summary["proof_rate_of_tested"] is None
    assert "n/a" in render(summary)
    # The diagnostics still say what happened.
    assert summary["genuinely_tested"] == 3
    assert summary["not_formalized"] == 1
    assert summary["suspect_unverified"] == 3


def test_the_denominator_is_never_silently_the_diagnostic_count():
    """They differ exactly when a suspect row is present, and that is the
    difference the whole distinction exists to hold."""
    summary = summarize([result(ProofOutcome.NOT_PROVED, "a"),
                         result(ProofOutcome.SUSPECT_STATEMENT, "b")])

    assert summary["genuinely_tested"] == 2
    assert summary["valid_proof_targets"] == 1
    assert summary["proof_rate_of_tested"] == 0.0, "one valid goal, unproved"


def test_the_headline_rate_is_never_moved_by_either_suspect_state():
    """`proof_rate` is the end-to-end number and stays agent-independent."""
    asserted = summarize([result(ProofOutcome.PROVED, "a"),
                          result(ProofOutcome.SUSPECT_STATEMENT, "b")])
    verified = summarize([result(ProofOutcome.PROVED, "a"),
                          result(ProofOutcome.REFUTED, "b")])

    assert asserted["proof_rate"] == verified["proof_rate"] == 0.5


def test_an_exhausted_run_is_excluded_too():
    summary = summarize([result(ProofOutcome.PROVED, "a"),
                         result(ProofOutcome.EXHAUSTED, "b")])

    assert summary["genuinely_tested"] == 1
    assert summary["proof_rate_of_tested"] == 1.0


def test_the_four_pilot_outcomes_produce_four_different_lines():
    """End to end, on the goals that caused all of this."""
    outcomes = [ProofOutcome.NOT_FORMALIZED, ProofOutcome.SUSPECT_STATEMENT,
                ProofOutcome.EXHAUSTED, ProofOutcome.NOT_PROVED]
    lines = [mark_for(o) for o in outcomes]

    assert len(set(lines)) == 4
    summary = summarize([result(o, goal_id=o.value) for o in outcomes])
    # Two, not one: the suspect row is an unverified report, so it is still a
    # goal the prover was asked to prove and did not. Only NOT_FORMALIZED and
    # EXHAUSTED come out here.
    assert summary["genuinely_tested"] == 2
    assert summary["proof_rate_of_tested"] == 0.0


# ------------------------------------------ 4. classification is NOT weakened
def test_reporting_changes_did_not_touch_what_counts_as_a_proof():
    """Requirement 7. The guard is elsewhere; this file must not have moved it."""
    from eval.proof_metrics import classify

    run = ProofRun(goal="q", statement="theorem t : True", statement_ok=True)
    run.trace.append("suspect statement: not connected")

    assert classify(run) is ProofOutcome.SUSPECT_STATEMENT
    assert not run.proved, "a report became a proof"


# ------------------------------- 5. `--goal` against a CUSTOM `--goals` file
def custom_goals(tmp_path, ids):
    """A goals file whose ids are deliberately absent from eval/proofs.json."""
    path = tmp_path / "custom.json"
    path.write_text(json.dumps([
        {"id": goal_id, "area": "proofnet 1", "goal": "Prove this.",
         "tier": "proofnet"}
        for goal_id in ids
    ]), encoding="utf-8")
    return path


def run_cli(monkeypatch, argv, seen):
    """`main()` with the prover stubbed. No model, no Lean."""
    import scripts.evaluate_proofs as cli
    from domain.proof import ProofRun

    def fake_prove(goal, **kwargs):
        seen.append(goal)
        return ProofRun(goal=goal, statement="theorem t : True", statement_ok=True)

    monkeypatch.setattr(cli, "prove", fake_prove)
    monkeypatch.setattr(cli, "lean_is_available", lambda: True)
    monkeypatch.setattr(sys, "argv", ["evaluate_proofs.py", *argv])
    return cli.main()


def test_a_goal_from_a_custom_file_is_not_checked_against_the_default_set(
        tmp_path, monkeypatch):
    """THE bug. `--goal` was validated with a second `load_goals()` call that
    took no argument, so every id from a `--goals` file was compared against
    `eval/proofs.json` and rejected — `exercise_1_1a` exists in
    `eval/proofnet-formal.json` and still printed "No such goal"."""
    goals_file = custom_goals(tmp_path, ["exercise_1_1a", "exercise_1_2"])
    seen = []

    code = run_cli(monkeypatch, [
        "--goals", str(goals_file), "--goal", "exercise_1_1a",
        "--out", str(tmp_path / "out.json"),
    ], seen)

    assert code != 2, "a goal present in the custom file was rejected"
    assert len(seen) == 1, f"{len(seen)} goals ran; expected exactly the one asked for"


def test_the_id_really_is_absent_from_the_default_dataset(tmp_path):
    """Guards the test above: if `exercise_1_1a` were ever added to
    eval/proofs.json the regression would pass for the wrong reason."""
    from eval.proof_dataset import load_goals as load

    assert "exercise_1_1a" not in {g.id for g in load()}


def test_an_id_in_no_file_at_all_is_still_rejected(tmp_path, monkeypatch):
    """The fix must not turn the validation off."""
    goals_file = custom_goals(tmp_path, ["exercise_1_1a"])
    seen = []

    code = run_cli(monkeypatch, [
        "--goals", str(goals_file), "--goal", "does_not_exist",
        "--out", str(tmp_path / "out.json"),
    ], seen)

    assert code == 2
    assert seen == [], "a run started despite an unknown goal id"


def test_several_goals_from_a_custom_file_are_all_selected(tmp_path, monkeypatch):
    goals_file = custom_goals(tmp_path, ["exercise_1_1a", "exercise_1_2",
                                         "exercise_1_18a"])
    seen = []

    code = run_cli(monkeypatch, [
        "--goals", str(goals_file),
        "--goal", "exercise_1_1a", "--goal", "exercise_1_18a",
        "--out", str(tmp_path / "out.json"),
    ], seen)

    assert code != 2
    assert len(seen) == 2


def test_the_default_dataset_still_works_without_goals(tmp_path, monkeypatch):
    """No `--goals`: validation must fall back to eval/proofs.json as before."""
    seen = []

    code = run_cli(monkeypatch, [
        "--goal", "num-two-plus-two", "--out", str(tmp_path / "out.json"),
    ], seen)

    assert code != 2
    assert len(seen) == 1
