"""The post-run reader, tested because my own tooling has been wrong before.

`scripts/read_run.py` computes four things the summary does not: the tier
gradient with source marked, the outcome mix inside each tier, whether a goal
walked away or was beaten, and whether context trimming could have engaged.
Each is a small arithmetic claim, and small arithmetic claims in this
project's own tooling have been wrong repeatedly -- `lemma_yield` sat at a
structurally impossible 0.0 for the project's entire history.
"""

import io
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import read_run  # noqa: E402


def goal(gid, tier, outcome, lean_calls=0, lean_budget=40, tokens=0, calls=0):
    return {"goal_id": gid, "tier": tier, "outcome": outcome,
            "lean_calls": lean_calls, "lean_budget": lean_budget,
            "input_tokens": tokens, "output_tokens": 0, "model_calls": calls,
            "cost_complete": True}


def render(data, against=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        read_run.gradient(data["results"])
        read_run.outcome_mix(data["results"])
        read_run.cost(data, data["results"])
        if against:
            read_run.compare(data, against)
    return out.getvalue()


def test_external_tiers_are_marked_as_such():
    """The single most important thing about a rate here, and it appears
    nowhere in the data: the curated tiers were written by this project."""
    text = render({"environment": {}, "results": [
        goal("a", "near-mathlib", "proved"),
        goal("b", "proofnet", "not_proved"),
    ]})

    assert "self-authored" in text
    assert "EXTERNAL" in text


def test_failures_before_proving_are_called_out():
    """MEASURED on ProofNet: 18 of 75 statements never elaborated and 17 more
    were flagged suspect, so 47% of failures happen before proving starts. A
    bare proof rate describes something else entirely."""
    text = render({"environment": {}, "results": [
        goal("a", "proofnet", "not_formalized"),
        goal("b", "proofnet", "suspect_statement"),
        goal("c", "proofnet", "proved"),
        goal("d", "proofnet", "not_proved"),
    ]})

    assert "failed BEFORE proving began" in text
    assert "2/4 (50%)" in text


def test_walking_away_is_distinguished_from_being_beaten():
    """An unproved goal that spent a fifth of its compiles chose to stop; one
    that spent them all was beaten."""
    text = render({"environment": {}, "results": [
        goal("walked", "hard", "not_proved", lean_calls=2, lean_budget=40),
        goal("beaten", "hard", "not_proved", lean_calls=38, lean_budget=40),
    ]})

    # one of the two counted as having walked away
    # tier n proved rate exhaust WALKED tokens source -> third from last
    row = [l for l in text.splitlines() if l.strip().startswith("hard")][0]
    assert row.split()[-3] == "1", row


def test_a_proved_goal_never_counts_as_walking_away():
    """Proving in two compiles is the best possible outcome."""
    text = render({"environment": {}, "results": [
        goal("quick", "hard", "proved", lean_calls=2, lean_budget=40),
    ]})

    row = [l for l in text.splitlines() if l.strip().startswith("hard")][0]
    assert row.split()[-3] == "0", row


def test_trimming_that_never_engaged_is_reported_as_such():
    """MEASURED: on the curated goals peak context stays under the 24,000
    trigger, so trimming was installed and inert. Reporting a saving there
    would be inventing one."""
    text = render({"environment": {"context_trim_trigger": 24000},
                   "results": [goal("a", "in-mathlib", "proved",
                                    tokens=40_000, calls=10)]})

    assert "never engaged" in text


def test_trimming_that_could_have_engaged_is_not_dismissed():
    text = render({"environment": {"context_trim_trigger": 24000},
                   "results": [goal("a", "putnam", "proved",
                                    tokens=300_000, calls=20)]})

    assert "never engaged" not in text
    assert "reached it on 1" in text


def test_a_floor_is_labelled_when_a_transcript_was_lost():
    """A run that crashed reports zero cost; totalling it silently would
    present a partial sum as the whole."""
    lost = goal("a", "putnam", "exhausted", tokens=0, calls=0)
    lost["cost_complete"] = False
    text = render({"environment": {}, "results": [lost]})

    assert "FLOOR" in text


def test_the_comparison_uses_only_shared_goals():
    """Comparing different problem sets is not a comparison."""
    new = {"environment": {}, "results": [
        goal("shared", "putnam", "proved", tokens=100),
        goal("only-new", "putnam", "proved", tokens=999),
    ]}
    old = {"results": [goal("shared", "putnam", "not_proved", tokens=200)]}

    text = render(new, against=old)

    assert "shared" in text
    assert "only-new" not in text
    assert "-50%" in text
