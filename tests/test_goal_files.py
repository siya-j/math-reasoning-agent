"""A goals file must have unique ids, because two things key on them.

MEASURED on eval/proofnet-v3-validation.json: 182 entries, 177 unique ids.
FIVE ids appear twice, and they are not duplicate rows -- they are DIFFERENT
THEOREMS that happen to share a number, because ProofNet numbers exercises
per textbook and `exercise_3_1` in one book collides with `exercise_3_1` in
another. The `open ...` preambles differ, and so do the statements.

Two things key on id and both break:

  * `--goal exercise_3_1` selects every row with that id, so one flag runs two
    different theorems and the results file gets two rows claiming to be the
    same goal.
  * `--resume` skips goals "already decided" by id, so deciding one collision
    silently skips the other forever.

Two of the five collisions -- `exercise_3_1` and `exercise_3_22` -- are
already decided in committed results, so this was not hypothetical.
`eval/proofnet-182.json` disambiguates them, keeping the copy that matches a
prior run under the original id so historical results still line up.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parent.parent / "eval"

# Files meant to be passed to `--goals`. Result files and samples are not.
GOAL_FILES = ["mixed-benchmark.json", "proofnet-182.json", "proofnet-4.json",
              "proofnet-formal.json", "proofs.json"]


def _goals(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    goals = data if isinstance(data, list) else data.get("goals", data)
    return goals if isinstance(goals, list) else []


@pytest.mark.parametrize("name", GOAL_FILES)
def test_ids_are_unique(name):
    path = EVAL / name
    if not path.exists():
        pytest.skip(f"{name} is not present")
    goals = _goals(path)
    assert goals, f"{name} parsed to no goals"

    repeated = {k: c for k, c in Counter(g.get("id") for g in goals).items()
                if c > 1}
    assert not repeated, (
        f"{name} repeats ids {repeated}. `--goal` would select several rows "
        f"under one flag and `--resume` would skip the rest forever. See "
        f"eval/proofnet-182.json for how the ProofNet collisions were "
        f"disambiguated."
    )


@pytest.mark.parametrize("name", GOAL_FILES)
def test_every_goal_has_an_id_and_a_tier(name):
    path = EVAL / name
    if not path.exists():
        pytest.skip(f"{name} is not present")
    for goal in _goals(path):
        assert (goal.get("id") or "").strip(), goal
        assert (goal.get("tier") or "").strip(), goal


def test_the_182_kept_the_ids_prior_runs_already_use():
    """The disambiguation must not rename a goal that committed results refer
    to, or every historical comparison silently stops matching."""
    path = EVAL / "proofnet-182.json"
    if not path.exists():
        pytest.skip("proofnet-182.json is not present")
    ids = {g["id"] for g in _goals(path)}

    decided = set()
    for results in (EVAL / "results").glob("*.json"):
        try:
            rows = json.loads(results.read_text(encoding="utf-8")).get("results", [])
        except (ValueError, OSError):
            continue
        decided |= {r["goal_id"] for r in rows
                    if r.get("tier") == "proofnet" and r.get("outcome") != "error"}

    missing = sorted(decided - ids)
    assert not missing, (
        f"these ProofNet goals appear in committed results but not in the "
        f"182-goal file, so a resume or comparison would not find them: "
        f"{missing}"
    )


def test_it_is_actually_bigger_than_what_it_replaces():
    """The point of the file. Twenty ProofNet goals put a 95% interval of
    roughly 38-88% around a 10/15 result, which cannot distinguish 67% from
    85%. This is the sample-size fix, so a shrinking file is a regression."""
    path = EVAL / "proofnet-182.json"
    if not path.exists():
        pytest.skip("proofnet-182.json is not present")
    mixed = _goals(EVAL / "mixed-benchmark.json")
    before = sum(1 for g in mixed if g.get("tier") == "proofnet")

    assert len(_goals(path)) >= before * 5, (
        f"{len(_goals(path))} goals against {before} before"
    )
