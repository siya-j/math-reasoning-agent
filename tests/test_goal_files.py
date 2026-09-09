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
GOAL_FILES = ["mixed-benchmark.json", "proofnet-sharp.json",
              "proofnet-182.json", "proofnet-4.json",
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


# ==================================================================
# The corrected benchmark
# ==================================================================
# MEASURED against PAug/ProofNetSharp (MIT), the corrected ProofNet from
# Poiroux et al.: of the 182 statements this project had been running, 67
# GENUINELY DIFFER from the corrected text, and 18 of those had already been
# decided in committed results. The differences are not cosmetic:
#
#   exercise_11_4_1b  ours: `Polynomial F` with `card F = 2`
#                     Sharp: `Polynomial ℚ`          -- a different theorem
#   exercise_1_1_16   ours: one direction
#                     Sharp: an iff                  -- strictly harder
#   exercise_1_19b    ours: `i * z / i^2`
#                     Sharp: `z^(i+1)/(i+1)^2`       -- ours is a typo'd series
#   exercise_1_19     ours: c and r given
#                     Sharp: existence AND uniqueness -- much harder
#
# Upstream reports mistakes in 118 of ProofNet's 371 entries. This project
# independently found 17% of the slice it touched to be broken or suspect,
# with two compiler-verified refutations, which is consistent with that.

def test_the_corrected_set_is_collision_free_by_construction():
    """ProofNet numbers exercises PER TEXTBOOK, so bare names collide -- 22 of
    them across the full 371. The hand-patched `proofnet-182.json` found only
    5 because it held half the data. ProofNetSharp qualifies every id with its
    source book, so the collision cannot recur."""
    goals = _goals(EVAL / "proofnet-sharp.json")
    assert len(goals) == 371, len(goals)

    bare = [g["id"].split("_", 1)[1] for g in goals]
    assert len(set(bare)) < len(bare), (
        "no bare-name collisions remain, so this file no longer demonstrates "
        "why the textbook prefix is needed"
    )
    assert len({g["id"] for g in goals}) == len(goals)


def test_ids_are_shell_safe():
    """`--goal Artin|exercise_2_3_2` would be a pipeline. The upstream pipe is
    replaced by an underscore so an id can be pasted into a command."""
    for goal in _goals(EVAL / "proofnet-sharp.json"):
        assert not set(goal["id"]) & set("|&;<>()$`\\\"' \t"), goal["id"]


def test_the_area_is_the_textbook_not_a_chapter_number():
    """`proofnet 3` says nothing. `Rudin` says what kind of mathematics this
    is, and makes the per-area proof rate readable."""
    areas = {g["area"] for g in _goals(EVAL / "proofnet-sharp.json")}
    assert "Rudin" in areas and "Munkres" in areas, sorted(areas)
    assert not any(a.startswith("proofnet") for a in areas), sorted(areas)


def test_the_informal_proof_is_carried_for_later():
    """369 of 371 entries ship a natural-language proof. Nothing reads it yet;
    it is what a HILBERT-style informal-reasoning leg would consume, and
    carrying it means that experiment needs no new download."""
    goals = _goals(EVAL / "proofnet-sharp.json")
    withproof = [g for g in goals if (g.get("informal_proof") or "").strip()]
    assert len(withproof) >= 360, len(withproof)
    assert all((g.get("informal") or "").strip() for g in goals[:20])


def test_the_loader_ignores_the_extra_keys():
    """`load_goals` reads named keys, so `informal`, `informal_proof`,
    `source_id` and `split` ride along without breaking anything."""
    from eval.proof_dataset import load_goals
    loaded = load_goals(EVAL / "proofnet-sharp.json")
    assert len(loaded) == 371
    assert loaded[0].note.strip()
