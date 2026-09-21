"""A results file must stay auditable on an OS that did not write it.

A results row keeps the theorem but not the `open` lines above it, so the
audit recovers them from the goal file the run recorded. Two ways that has
already failed, both MEASURED on the real corpus:

1. NOT RECOVERED AT ALL. `verify_results.py` compiled every claim against a
   bare `import Mathlib`, so `finrank`, `Tendsto`, `End`, `Icc`, `univ` and
   `𝓟` came back as unknown identifiers and seven claims in
   `heldout-even-63.json` were reported as soundness failures. Two of them
   recompiled untouched once the opens were restored.

2. RECOVERED ON THE WRONG OS. Runs recorded on Windows store the path as
   `eval\\proofnet-sharp.json`. Under WSL that is a single filename with a
   backslash in it, so the lookup missed silently and the opens were lost
   again -- 8 claims across 4 files, every one of them a missing `open`
   reported as "A claim that does not recompile is a soundness failure".

The second is the same Windows/POSIX boundary that had `usage_examples.find`
emitting `Mathlib\\Analysis\\Basic.lean` into a model-facing citation.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import verify_results  # noqa: E402
from math_v2.core import preamble  # noqa: E402

GOAL = {
    "id": "exercise_1_1",
    "note": (
        "import Mathlib\n\n"
        "open Filter Set Topology\n"
        "open scoped BigOperators\n\n"
        "theorem exercise_1_1 : True := sorry"
    ),
}


def _goals_file(tmp_path):
    path = tmp_path / "eval" / "goals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([GOAL]), encoding="utf-8")
    return path


def test_the_opens_are_recovered_from_the_recorded_goal_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _goals_file(tmp_path)

    found = verify_results.preambles_for({"run": {"goals_file": "eval/goals.json"}})

    assert "open Filter Set Topology" in found["exercise_1_1"]
    assert "open scoped BigOperators" in found["exercise_1_1"]


def test_a_windows_recorded_path_still_resolves(tmp_path, monkeypatch):
    """`eval\\goals.json` is a PATH, not a filename with a backslash in it."""
    monkeypatch.chdir(tmp_path)
    _goals_file(tmp_path)

    found = verify_results.preambles_for({"run": {"goals_file": "eval\\goals.json"}})

    assert found, "the Windows-separated path did not resolve"
    assert "open Filter Set Topology" in found["exercise_1_1"]


# ------------------------------------------------- losing the opens is not fatal
def test_a_goal_file_that_has_moved_costs_the_opens_not_the_audit(tmp_path,
                                                                  monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert verify_results.preambles_for({"run": {"goals_file": "eval/gone.json"}}) == {}


def test_a_run_that_recorded_no_goal_file_is_handled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert verify_results.preambles_for({}) == {}
    assert verify_results.preambles_for({"run": {}}) == {}


def test_an_unparseable_goal_file_is_handled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "eval" / "goals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    assert verify_results.preambles_for({"run": {"goals_file": "eval/goals.json"}}) == {}


def test_a_claim_with_no_recovered_preamble_still_compiles_against_the_base():
    """The fallback is exactly the behaviour before any of this existed."""
    claim = {"statement": "theorem g (n : Nat) : n + 0 = n", "proof": "by simp",
             "lemmas": []}
    assert preamble.BASE in verify_results.source_for(claim)


# ------------------------------------------- the row carries its own preamble
def test_a_written_result_records_the_preamble_it_was_compiled_under():
    """So a row is recheckable with nothing but itself.

    Storing the statement and its opens APART is the defect that has now
    shipped three times; this is what stops the audit needing a goal file at
    all.
    """
    from domain.proof import ProofRun
    from eval.proof_dataset import Goal, Tier
    from eval.proof_metrics import result_from

    goal = Goal(id="exercise_1_1", area="Rudin", goal="prove it",
                tier=Tier.PROOFNET, note=GOAL["note"])
    run = ProofRun(goal="prove it")
    run.statement = "theorem exercise_1_1 : True"

    row = result_from(goal, run)

    assert "open Filter Set Topology" in row.preamble
    assert row.preamble.startswith(preamble.BASE)


def test_the_row_beats_the_goal_file(tmp_path, monkeypatch):
    """The row is authoritative: it is what that run actually compiled under.

    Pointed at a goal file with DIFFERENT opens, the row must still win --
    otherwise a goal file edited after the fact silently rewrites history.
    """
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "eval" / "goals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([
        dict(GOAL, note="import Mathlib\n\nopen SomethingElse\n")
    ]), encoding="utf-8")

    data = {
        "run": {"goals_file": "eval/goals.json"},
        "results": [{
            "goal_id": "exercise_1_1", "outcome": "proved",
            "statement": "theorem exercise_1_1 : True", "proof": "by trivial",
            "lemmas": [], "preamble": "import Mathlib\nopen Filter Set\n",
        }],
    }
    (tmp_path / "results.json").write_text(json.dumps(data), encoding="utf-8")

    claim = verify_results.claims_in(tmp_path / "results.json")[0]

    assert "open Filter Set" in claim["preamble"]
    assert "SomethingElse" not in claim["preamble"], "the goal file overrode the row"


def test_a_row_without_a_preamble_still_falls_back_to_the_goal_file(tmp_path,
                                                                    monkeypatch):
    """Everything written before the field existed must stay auditable."""
    monkeypatch.chdir(tmp_path)
    _goals_file(tmp_path)
    data = {
        "run": {"goals_file": "eval/goals.json"},
        "results": [{
            "goal_id": "exercise_1_1", "outcome": "proved",
            "statement": "theorem exercise_1_1 : True", "proof": "by trivial",
            "lemmas": [],
        }],
    }
    (tmp_path / "results.json").write_text(json.dumps(data), encoding="utf-8")

    claim = verify_results.claims_in(tmp_path / "results.json")[0]

    assert "open Filter Set Topology" in claim["preamble"]
