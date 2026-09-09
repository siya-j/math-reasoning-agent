"""Three failures in a row must stop the run, however they are reported.

MEASURED on eval/results/proofnet-60.json. An INVALID -- not missing -- API
key let the model BUILD and then fail at call time. `math_v2.harness` catches
its own exceptions ("a crash must not lose the record") and returns a ProofRun
whose trace says "agent failed", so `prove()` returned normally, the abort
never saw an exception, and ALL 53 remaining goals ran and errored identically
in zero seconds.

The first time this happened the key was ABSENT, `get_model()` raised at build
time, and the abort worked exactly as designed -- which is why the hole went
unnoticed for so long.

Nothing was billed that time, because no call ever succeeded. The case that
would cost real money is a quota or auth failure part-way through a paid run,
and this counter is the only thing that stops it.
"""

import pytest

from eval.proof_dataset import Tier
from eval.proof_metrics import ProofOutcome, ProofResult


class Goal:
    def __init__(self, index):
        self.id, self.area, self.tier = f"g{index}", "proofnet", Tier.PROOFNET
        self.goal = "prove something"


def _drive(outcomes, monkeypatch, tmp_path):
    """Run the evaluator's loop against a prover with scripted outcomes."""
    import scripts.evaluate_proofs as ev

    seen = []
    supply = iter(outcomes)

    def fake_prove(goal_text, **kwargs):
        seen.append(goal_text)
        from domain.proof import ProofRun
        outcome = next(supply, ProofOutcome.ERROR)
        trace = ["agent failed: AuthError"] if outcome is ProofOutcome.ERROR else []
        return ProofRun(goal="q", statement="theorem t : True", trace=trace)

    def fake_result_from(goal, run):
        return ProofResult(goal_id=goal.id, area=goal.area, tier=goal.tier,
                           outcome=(ProofOutcome.ERROR
                                    if any(t.startswith("agent failed")
                                           for t in run.trace)
                                    else ProofOutcome.NOT_PROVED))

    monkeypatch.setattr(ev, "prove", fake_prove)
    monkeypatch.setattr(ev, "result_from", fake_result_from)
    monkeypatch.setattr(ev, "load_goals", lambda *a, **k: [Goal(i) for i in range(10)])
    monkeypatch.setattr(ev, "lean_is_available", lambda: True)
    monkeypatch.setattr(ev, "environment", lambda: {})
    # `main()` reads sys.argv rather than taking arguments.
    monkeypatch.setattr("sys.argv",
                        ["evaluate_proofs.py", "--out", str(tmp_path / "out.json")])
    ev.main()
    return seen


def test_three_returned_errors_stop_the_run(monkeypatch, tmp_path):
    """THE regression. Every goal errors, and the run must not walk the whole
    list -- 53 goals did exactly that."""
    seen = _drive([ProofOutcome.ERROR] * 10, monkeypatch, tmp_path)

    import scripts.evaluate_proofs as ev
    assert len(seen) == ev.CONSECUTIVE_ERROR_LIMIT, (
        f"ran {len(seen)} goals after {ev.CONSECUTIVE_ERROR_LIMIT} straight "
        f"failures"
    )


def test_a_success_resets_the_counter(monkeypatch, tmp_path):
    """Intermittent faults must not abort a healthy run. Two errors, a
    success, then two more errors is four failures but never three in a row."""
    seen = _drive([ProofOutcome.ERROR, ProofOutcome.ERROR,
                   ProofOutcome.NOT_PROVED,
                   ProofOutcome.ERROR, ProofOutcome.ERROR,
                   ProofOutcome.NOT_PROVED,
                   ProofOutcome.NOT_PROVED, ProofOutcome.NOT_PROVED,
                   ProofOutcome.NOT_PROVED, ProofOutcome.NOT_PROVED],
                  monkeypatch, tmp_path)
    assert len(seen) == 10, "an intermittent fault aborted a healthy run"


def test_a_clean_run_is_untouched(monkeypatch, tmp_path):
    seen = _drive([ProofOutcome.NOT_PROVED] * 10, monkeypatch, tmp_path)
    assert len(seen) == 10
