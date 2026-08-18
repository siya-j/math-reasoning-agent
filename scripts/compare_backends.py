"""The seven near-Mathlib goals, both Lean backends, one variable changed.

    python scripts/compare_backends.py

NOT A GATE, AND IT USED TO BE ONE. THAT WAS A MISTAKE.

This reports; it does not judge. Per-goal outcome equality is not a valid
acceptance test for a stochastic agent, and requiring it here conflated two
different questions:

  BACKEND CORRECTNESS  does Lean classify the same SOURCE the same way?
      Deterministic, so equality IS valid. Tested by
      `scripts/compare_lean_modes.py` (fixed snippets) and
      `scripts/replay_sources.py` (every source a real run produced).

  AGENT PERFORMANCE    what does a cheaper compiler buy the agent?
      Stochastic. Per-goal outcomes need NOT match and it is a mistake to
      demand it.

MEASURED, from the first run of this script. Subprocess 71% / REPL 86%, and
the arms were not comparable:

    subprocess   50 model calls   15 lean calls   1387s
    repl         72 model calls   33 lean calls    276s

The subprocess arm's own trace says why —

    budget: 293s of the 300s budget used - under 60s left,
            too little to finish a compilation (slowest seen here: 52s)

— it ran out of WALL CLOCK, not of compiles. Both arms were allowed 8
compilations; the slow one could not afford them. So the two arms ran
different experiments, and the honest reading of 71% vs 86% is not "the REPL
is better at mathematics" but "the subprocess arm was starved". Which is a
finding about the BUDGET, and worth having, but it is not this script's
verdict to give.

It therefore always exits 0. Read the numbers.

ONE VARIABLE. Model, prover, budgets, retrieval and agent steps are whatever
the environment says; only MRA_LEAN_BACKEND changes between the two arms. It
is deliberately not configurable from the command line, because a comparison
where the operator can vary a second thing is not a comparison.

This costs real model calls: seven goals twice. The subprocess arm is the slow
one — roughly 40s per compile — so budget an hour or so.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = ROOT / "eval" / "results"

# Everything that is NOT the variable under test. Set here rather than
# inherited so an operator's leftover shell cannot silently change one arm.
FIXED = {
    "MRA_PROVER": "math_v2",
    "MRA_EXEC": "local",
}


def run_arm(backend: str) -> tuple[Path, float]:
    out = RESULTS / f"near-mathlib-{backend}.json"
    environment = dict(os.environ)
    environment.update(FIXED)
    environment["MRA_LEAN_BACKEND"] = backend
    # The alias would override nothing, but leaving it set while testing the
    # subprocess arm would be a trap worth removing rather than documenting.
    environment.pop("MRA_LEAN_REPL", None)

    print(f"\n{'=' * 70}")
    print(f"  ARM: lean backend = {backend}")
    print(f"{'=' * 70}")

    started = time.time()
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "evaluate_proofs.py"),
         "--tier", "near-mathlib", "--out", str(out)],
        cwd=ROOT, env=environment, check=False,
    )
    return out, time.time() - started


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def compare(a: dict, b: dict) -> list[str]:
    """Per-goal differences. INFORMATION, not failures — see the module
    docstring. A different trajectory is the expected consequence of a
    different compile budget, not evidence about Lean."""
    left = {r["goal_id"]: r for r in a.get("results", [])}
    right = {r["goal_id"]: r for r in b.get("results", [])}
    problems = []

    for goal_id in sorted(set(left) | set(right)):
        one, two = left.get(goal_id), right.get(goal_id)
        if one is None or two is None:
            problems.append(f"{goal_id}: present in only one arm")
            continue
        if one["outcome"] != two["outcome"]:
            problems.append(
                f"{goal_id}: subprocess={one['outcome']} repl={two['outcome']}")
    return problems


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)

    if not os.getenv("MRA_LEAN_PROJECT") and not os.getenv("LEAN_WORKSPACE"):
        print("Set MRA_LEAN_PROJECT to the Lake project with Mathlib.")
        return 2
    if not os.getenv("MRA_LEAN_REPL_BIN"):
        print("Set MRA_LEAN_REPL_BIN to the built repl binary.")
        return 2

    sub_path, sub_seconds = run_arm("subprocess")
    repl_path, repl_seconds = run_arm("repl")

    sub, repl = load(sub_path), load(repl_path)
    if not sub or not repl:
        print("\nOne of the arms produced no results. Nothing to compare.")
        return 1

    print(f"\n{'=' * 70}")
    print("  PER-GOAL OUTCOMES")
    print(f"{'=' * 70}")
    left = {r["goal_id"]: r for r in sub.get("results", [])}
    right = {r["goal_id"]: r for r in repl.get("results", [])}
    print(f"  {'goal':<34} {'subprocess':<20} {'repl':<20}")
    print("  " + "-" * 66)
    for goal_id in sorted(set(left) | set(right)):
        one = left.get(goal_id, {}).get("outcome", "-")
        two = right.get(goal_id, {}).get("outcome", "-")
        flag = "" if one == two else "   *** DIFFER ***"
        print(f"  {goal_id:<34} {one:<20} {two:<20}{flag}")

    print(f"\n{'=' * 70}")
    print("  SUMMARY — the numbers the A/B is for")
    print(f"{'=' * 70}")
    rows = [
        ("proof rate", "proof_rate"),
        ("formalisation rate", "formalization_rate"),
        ("proof rate | tested", "proof_rate_of_tested"),
        ("not formalized", "not_formalized"),
        ("statement suspect", "suspect_statements"),
        ("budget exhausted", "exhausted"),
        ("genuinely tested", "genuinely_tested"),
        ("mean attempts", "mean_attempts"),
        ("errors", "errors"),
    ]
    print(f"  {'metric':<26} {'subprocess':<16} {'repl':<16}")
    print("  " + "-" * 58)
    for label, key in rows:
        print(f"  {label:<26} {str(sub['summary'].get(key)):<16} "
              f"{str(repl['summary'].get(key)):<16}")

    def total(results, field):
        return sum(r.get(field, 0) or 0 for r in results)

    print("  " + "-" * 58)
    for label, field in (("model calls", "model_calls"),
                         ("lean calls", "lean_calls"),
                         ("retrieval calls", "retrieval_calls")):
        print(f"  {label:<26} {total(sub['results'], field):<16} "
              f"{total(repl['results'], field):<16}")
    print(f"  {'wall clock (s)':<26} {sub_seconds:<16.0f} {repl_seconds:<16.0f}")
    goals = max(len(sub.get("results", [])), 1)
    print(f"  {'seconds per goal':<26} {sub_seconds / goals:<16.0f} "
          f"{repl_seconds / goals:<16.0f}")
    if repl_seconds > 0:
        print(f"\n  {sub_seconds / repl_seconds:.1f}x faster end to end")

    print(f"\n  environment recorded:")
    for arm, data in (("subprocess", sub), ("repl", repl)):
        print(f"    {arm:<12} {data.get('environment', {})}")

    differences = compare(sub, repl)
    print()
    if differences:
        print("Per-goal differences (EXPECTED — the arms explore differently):")
        for difference in differences:
            print(f"  - {difference}")
        print()
        print("This is not a failure. Whether the BACKENDS agree is settled by")
        print("scripts/replay_sources.py, which recompiles every source a real")
        print("run produced through both and costs no model calls.")
    else:
        print("Both arms happened to reach the same outcome on every goal.")
        print("With a stochastic agent that is luck, not proof of equivalence.")

    starved = [r for r in sub.get("results", [])
               if any("budget:" in entry or "stopped early" in entry
                      for entry in (r.get("trace") or []))]
    if starved:
        print()
        print(f"NOTE: {len(starved)} subprocess goal(s) hit a budget limit. The")
        print("arms are then not comparable on outcome, only on cost — a goal")
        print("that ran out of clock was not measured, it was interrupted.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
