"""Retiring a goal is destroying evidence, so the rules have to hold.

`eval/proofs.json` -- the default goal file -- read as 100% proved and looked
ready to retire whole. Counting every attempt instead of the latest one, it
is 141/166 = 85%, and nine of its goals still fail regularly. Retiring the
TIERS would have thrown those away along with the spent ones.

These tests guard the two ways this can go wrong: retiring a goal that still
carries signal, and retiring on too little evidence to know.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location(
        "retire_saturated", ROOT / "scripts" / "retire_saturated.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOALS = [
    {"id": "spent", "area": "a", "goal": "g", "tier": "in-mathlib"},
    {"id": "flaky", "area": "a", "goal": "g", "tier": "hard"},
    {"id": "thin", "area": "a", "goal": "g", "tier": "deep"},
    {"id": "never-run", "area": "a", "goal": "g", "tier": "novel"},
]
HISTORY = {
    "spent": (6, 6),      # perfect, and plenty of runs
    "flaky": (3, 8),      # fails more often than not
    "thin": (2, 2),       # perfect, but only twice
}


def test_a_goal_that_ever_failed_is_never_retired():
    """The whole point. `hard-sum-odd-squares` is 3/8 -- it has headroom, so
    it is exactly where an improvement would become visible."""
    spent, live, _ = _mod().split(GOALS, HISTORY, min_attempts=4)
    assert [g["id"] for g in spent] == ["spent"]
    assert "flaky" in [g["id"] for g in live]


def test_a_perfect_record_on_too_few_runs_does_not_retire():
    """Repeated goals here agree only 44% of the time, so two passes is not
    evidence of saturation. MEASURED consequence: four `deep` goals sit at
    3/3, and retiring on that would have dropped the most expensive tier in
    the set on three runs of evidence."""
    spent, _, unmeasured = _mod().split(GOALS, HISTORY, min_attempts=4)
    assert "thin" not in [g["id"] for g in spent]
    assert "thin" in [g["id"] for g in unmeasured]


def test_a_goal_with_no_history_is_kept():
    """Silence is not evidence. A goal never run must not be retired for it."""
    _, _, unmeasured = _mod().split(GOALS, HISTORY, min_attempts=4)
    assert "never-run" in [g["id"] for g in unmeasured]


def test_no_goal_is_lost_or_duplicated():
    """Between them the three buckets must be a partition -- a retirement
    that silently drops a goal is worse than one that retires too few."""
    spent, live, unmeasured = _mod().split(GOALS, HISTORY, min_attempts=4)
    ids = [g["id"] for g in spent + live + unmeasured]
    assert sorted(ids) == sorted(g["id"] for g in GOALS)
    assert len(ids) == len(set(ids))


def test_an_error_row_is_not_evidence_about_the_goal(tmp_path, monkeypatch):
    """An `error` outcome means the harness fell over -- a billing cap, a
    transient API failure. Counting it as a miss would keep a spent goal
    alive; counting it as a pass would retire a live one."""
    module = _mod()
    results = tmp_path / "eval" / "results"
    results.mkdir(parents=True)
    (results / "r.json").write_text(json.dumps({"results": [
        {"goal_id": "g", "outcome": "proved"},
        {"goal_id": "g", "outcome": "error"},
        {"goal_id": "g", "outcome": "error"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert module.record() == {"g": (1, 1)}, "errors must not count either way"


def test_the_report_writes_nothing_by_default(capsys):
    """Retiring goals changes what every later run measures, so it may not
    happen as a side effect of asking what would be retired."""
    module = _mod()
    before = {p: p.read_bytes() for p in (module.CANARY, module.LIVE)
              if p.exists()}
    assert module.main([]) == 0
    assert "REPORT ONLY" in capsys.readouterr().out
    for path, content in before.items():
        assert path.read_bytes() == content, f"{path.name} was modified"


def test_the_two_files_together_are_the_original_set():
    """Nothing may be invented and nothing dropped: canary + live == the
    goals we started with. Checked against the files actually on disk."""
    module = _mod()
    if not (module.CANARY.exists() and module.LIVE.exists()):
        import pytest
        pytest.skip("run scripts/retire_saturated.py --write first")
    original = {g["id"] for g in json.loads(
        module.GOALS.read_text(encoding="utf-8"))}
    canary = {g["id"] for g in json.loads(
        module.CANARY.read_text(encoding="utf-8"))}
    live = {g["id"] for g in json.loads(
        module.LIVE.read_text(encoding="utf-8"))}
    assert canary | live == original
    assert not (canary & live), "a goal cannot be both retired and live"


def test_the_retired_file_holds_nothing_that_ever_failed():
    """The strongest statement this can make, checked against the real run
    history rather than a fixture."""
    module = _mod()
    if not module.CANARY.exists():
        import pytest
        pytest.skip("run scripts/retire_saturated.py --write first")
    history = module.record()
    for goal in json.loads(module.CANARY.read_text(encoding="utf-8")):
        hit, total = history.get(goal["id"], (0, 0))
        assert total >= module.MIN_ATTEMPTS, f"{goal['id']} retired on {total} runs"
        assert hit == total, f"{goal['id']} is {hit}/{total} and still fails"
