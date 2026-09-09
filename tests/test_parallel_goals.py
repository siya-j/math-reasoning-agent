"""Running goals concurrently must not change what a run MEASURES.

WHY THIS AXIS, AND NOT PARALLEL LEAN. The plan for this work said "compiles
are serial and Lean dominates wall clock". MEASURED over 59 goal-runs
carrying both wall clock and call counts, that is false:

  * 1,454 model calls at a median 4.7s each account for ~80% of 8,486s.
  * 540 Lean calls account for 2-13%. `slowest_lean` over 76 surviving
    budget states has a MEDIAN of 0.3s -- the REPL imports Mathlib once and
    every compile after it is warm.

So the clock is bought waiting on the model, and the only useful parallelism
is across goals. Threads rather than processes is forced, not chosen:
`_repl.release_for_subprocess` records that TWO LEAN PROCESSES CANNOT BOTH
HOLD MATHLIB.

The risk this file guards is that `--workers` becomes a second run loop that
drifts from the first. This file has already been broken once by exactly
that shape of bug -- `MARKS` covered three of six outcomes and killed a live
run on its first goal.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.proof_dataset import Goal, Tier
from eval.proof_metrics import ProofOutcome
from scripts import evaluate_proofs


class FakeTelemetry:
    def summary(self):
        return "fake"


def _goals(n):
    return [Goal(id=f"g{i}", area="analysis", goal=f"prove {i}",
                 tier=Tier.PROOFNET) for i in range(n)]


def _args(**kw):
    base = dict(depth=None, workers=1, pace=0.0)
    base.update(kw)
    return types.SimpleNamespace(**base)


# --------------------------------------------------- one loop, not two
def test_both_paths_go_through_the_same_attempt_function():
    """The whole defence against the two loops drifting apart. If a future
    edit inlines the work back into either branch, this fails."""
    source = Path(evaluate_proofs.__file__).read_text(encoding="utf-8")
    body = source[source.index("if args.workers > 1:"):]
    concurrent, sequential = body.split("    else:", 1)
    assert "_attempt" in concurrent, "the concurrent path must call _attempt"
    assert "_attempt" in sequential, "the sequential path must call _attempt"
    assert concurrent.count("prove(") == 0, "it must not call prove directly"
    assert sequential.count("prove(") == 0


def test_a_raised_exception_becomes_an_error_result_not_a_crash(monkeypatch):
    """One goal failing must not end the run, on either path."""
    def boom(*a, **k):
        raise RuntimeError("the model is unreachable")
    monkeypatch.setattr(evaluate_proofs, "prove", boom)

    lines = []
    result, run = evaluate_proofs._attempt(
        _goals(1)[0], _args(), None, 1, 1, lines.append)
    assert result.outcome is ProofOutcome.ERROR
    assert run is None
    assert "the model is unreachable" in result.detail
    assert any("ERROR" in line for line in lines)


def test_output_is_emitted_not_printed(monkeypatch, capsys):
    """Concurrent goals printing directly interleave into soup. `_attempt`
    must route every line through `emit` so the caller can buffer it."""
    monkeypatch.setattr(evaluate_proofs, "prove",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("x")))
    lines = []
    evaluate_proofs._attempt(_goals(1)[0], _args(), None, 1, 1, lines.append)
    assert lines, "nothing was emitted"
    assert capsys.readouterr().out == "", "it printed instead of emitting"


def test_progress_callbacks_also_go_through_emit(monkeypatch):
    """The `progress=` callback is the chattiest source of output during a
    goal, so it is the one that would interleave worst."""
    def prove_with_progress(goal, depth=None, progress=None, reviewer=None):
        progress("searching Mathlib")
        progress("compiling")
        raise RuntimeError("stop")
    monkeypatch.setattr(evaluate_proofs, "prove", prove_with_progress)

    lines = []
    evaluate_proofs._attempt(_goals(1)[0], _args(), None, 1, 1, lines.append)
    assert any("searching Mathlib" in line for line in lines)
    assert any("compiling" in line for line in lines)


# --------------------------------------------------- the contradictory flags
def test_pace_and_workers_are_refused_together(monkeypatch, capsys):
    """`--pace` spaces goals out to respect a rate limit; `--workers` runs
    them together. Honouring one silently would make the other a lie."""
    monkeypatch.setattr(sys, "argv",
                        ["evaluate_proofs.py", "--workers", "4", "--pace", "5"])
    assert evaluate_proofs.main() == 2
    out = capsys.readouterr().out
    assert "contradictory" in out


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_workers_below_one_is_refused(monkeypatch, capsys, bad):
    monkeypatch.setattr(sys, "argv",
                        ["evaluate_proofs.py", "--workers", bad])
    assert evaluate_proofs.main() == 2
    assert "at least 1" in capsys.readouterr().out


def test_the_default_is_one_worker():
    """`--workers` must be opt-in. The default has to be the path every
    number in eval/results/ was produced by."""
    source = Path(evaluate_proofs.__file__).read_text(encoding="utf-8")
    assert '"--workers", type=int, default=1' in source


def test_the_help_says_it_does_not_save_money():
    """The binding constraint on this project is API spend, not wall clock.
    A flag that makes runs finish sooner without costing less must say so,
    or it reads as a cost saving."""
    source = Path(evaluate_proofs.__file__).read_text(encoding="utf-8")
    assert "does NOT reduce token cost" in source


# --------------------------------------------------- does it actually overlap
#
# Counted rather than timed. A timing assertion ("concurrent is faster")
# is flaky on a loaded machine and proves nothing about WHY; the peak number
# of goals in flight is exact.
def _drive(tmp_path, monkeypatch, workers, count=6, outcomes=None):
    """Run `main()` over `count` fake goals and report what happened."""
    import json
    import threading

    tmp_path.mkdir(parents=True, exist_ok=True)
    goals_file = tmp_path / "goals.json"
    goals_file.write_text(json.dumps([
        {"id": f"g{i}", "area": "analysis", "goal": f"prove {i}",
         "tier": "proofnet"} for i in range(count)
    ]), encoding="utf-8")
    out = tmp_path / "out.json"

    live = {"now": 0, "peak": 0}
    guard = threading.Lock()

    def fake_prove(goal, depth=None, progress=None, reviewer=None):
        with guard:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        progress("working")
        time.sleep(0.25)          # long enough for the pool to fill
        with guard:
            live["now"] -= 1
        return types.SimpleNamespace(telemetry=FakeTelemetry())

    monkeypatch.setattr(evaluate_proofs, "prove", fake_prove)

    order = iter(outcomes or [])

    def fake_result_from(goal, run):
        from eval.proof_metrics import ProofResult
        outcome = next(order, ProofOutcome.PROVED)
        return ProofResult(goal_id=goal.id, area=goal.area, tier=goal.tier,
                           outcome=outcome)

    monkeypatch.setattr(evaluate_proofs, "result_from", fake_result_from)
    monkeypatch.setattr(sys, "argv", [
        "evaluate_proofs.py", "--goals", str(goals_file),
        "--out", str(out), "--workers", str(workers),
    ])
    code = evaluate_proofs.main()
    saved = json.loads(out.read_text(encoding="utf-8"))
    return code, live["peak"], saved


import time  # noqa: E402  - used by the fake prove above


def test_four_workers_really_run_goals_at_the_same_time(tmp_path, monkeypatch):
    _, peak, saved = _drive(tmp_path, monkeypatch, workers=4)
    assert peak > 1, "nothing overlapped -- the pool ran them one at a time"
    assert peak <= 4, f"more goals in flight ({peak}) than workers allowed"
    assert len(saved["results"]) == 6, "a goal was lost"


def test_one_worker_never_overlaps(tmp_path, monkeypatch):
    """The default must stay strictly sequential -- every number in
    eval/results/ was produced that way."""
    _, peak, saved = _drive(tmp_path, monkeypatch, workers=1)
    assert peak == 1, f"the default path overlapped {peak} goals"
    assert len(saved["results"]) == 6


def test_concurrency_does_not_change_the_summary(tmp_path, monkeypatch):
    """Same goals, same outcomes, same numbers. If running them together
    changed the measurement, the flag would be worthless."""
    outcomes = [ProofOutcome.PROVED, ProofOutcome.EXHAUSTED,
                ProofOutcome.PROVED, ProofOutcome.REFUTED,
                ProofOutcome.PROVED, ProofOutcome.EXHAUSTED]
    _, _, one = _drive(tmp_path / "a", monkeypatch, 1, outcomes=list(outcomes))
    _, _, many = _drive(tmp_path / "b", monkeypatch, 4, outcomes=list(outcomes))

    def comparable(saved):
        return {k: v for k, v in saved["summary"].items()
                if not isinstance(v, float) or "rate" in k}

    assert comparable(one) == comparable(many)
    assert ({r["goal_id"] for r in one["results"]}
            == {r["goal_id"] for r in many["results"]})


def test_every_goal_is_attempted_exactly_once(tmp_path, monkeypatch):
    """A pool that submitted a goal twice would double-bill it."""
    _, _, saved = _drive(tmp_path, monkeypatch, workers=3, count=7)
    ids = [r["goal_id"] for r in saved["results"]]
    assert sorted(ids) == sorted(f"g{i}" for i in range(7))
    assert len(ids) == len(set(ids)), "a goal was run more than once"


def test_the_error_abort_still_fires_when_concurrent(tmp_path, monkeypatch,
                                                     capsys):
    """The guard that exists to stop PAYING for a broken run. An invalid API
    key once let all 53 remaining goals run and error in zero seconds; that
    must not become reachable again by passing --workers."""
    errors = [ProofOutcome.ERROR] * 8
    _drive(tmp_path, monkeypatch, workers=2, count=8, outcomes=errors)
    out = capsys.readouterr().out
    assert "Aborting" in out
    assert "consecutive errors" in out

