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
    assert mark_for(ProofOutcome.SUSPECT_STATEMENT) == "SUSPECT STATEMENT"


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


# --------------------------------- 3. and it is not counted as a proof failure
def test_a_suspect_statement_is_excluded_from_the_prover_denominator():
    """Requirement 4, restated as arithmetic. One proof, one suspect row: the
    prover was tested ONCE and succeeded, so the rate is 100%, not 50%."""
    summary = summarize([result(ProofOutcome.PROVED, "a"),
                         result(ProofOutcome.SUSPECT_STATEMENT, "b")])

    assert summary["genuinely_tested"] == 1
    assert summary["proof_rate_of_tested"] == 1.0
    assert summary["suspect_statements"] == 1
    # The blunt rate still counts it, deliberately — it answers a different
    # question ("of everything we ran") and both belong in the report.
    assert summary["proof_rate"] == 0.5


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
    assert summary["genuinely_tested"] == 1, "the prover was tested once, not four times"
    assert summary["proof_rate_of_tested"] == 0.0


# ------------------------------------------ 4. classification is NOT weakened
def test_reporting_changes_did_not_touch_what_counts_as_a_proof():
    """Requirement 7. The guard is elsewhere; this file must not have moved it."""
    from eval.proof_metrics import classify

    run = ProofRun(goal="q", statement="theorem t : True", statement_ok=True)
    run.trace.append("suspect statement: not connected")

    assert classify(run) is ProofOutcome.SUSPECT_STATEMENT
    assert not run.proved, "a report became a proof"
