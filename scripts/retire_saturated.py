"""Which of our own goals still teach us anything, and which only cost money.

THE MEASUREMENT THAT PROMPTED THIS

`eval/proofs.json` -- the file `evaluate_proofs.py` uses when no `--goals` is
given -- is 25 goals, all five of them tiers we wrote for ourselves. Taking
the latest outcome per goal across every results file on disk, all 25 are
proved: 100%. That reading suggests retiring the whole file.

It is the wrong reading. Counting EVERY attempt rather than the last one,
they are 141/166 = 85%, and the goals split cleanly in two:

  * 16 have a perfect record over every run they appeared in -- all six
    in-mathlib, `num-primes-strictly-above` 13/13, `top-compact-image` 11/11.
    A goal that has never failed cannot register an improvement, and can only
    register a regression severe enough that anything would catch it.

  * 9 still fail regularly -- `deep-nielsen-schreier` 1/4,
    `hard-sum-odd-squares` 3/8, `lin-vector-space-basis` 6/12. Four of the
    five `hard` goals sit below 90%. These have headroom and still carry
    signal.

So retiring the TIERS would have thrown away the 9. This script retires the
GOALS the evidence says are spent, and nothing else.

WHY A SCRIPT AND NOT A HAND-PICKED LIST

Saturation is a property of the run history, not of the goal, and the history
grows. A list I typed today is stale after the next run; this re-derives it.
And a goal is only called spent on `--min-attempts` runs of evidence, so one
lucky pass never retires anything.

    python scripts/retire_saturated.py                 # report, writes nothing
    python scripts/retire_saturated.py --write         # write the two files
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOALS = ROOT / "eval" / "proofs.json"
CANARY = ROOT / "eval" / "proofs-canary.json"
LIVE = ROOT / "eval" / "proofs-live.json"

# Four runs before a perfect record counts as evidence. Repeated goals in
# this project agree only 44% of the time (scripts/variance.py), so a goal
# that passed once or twice has told us almost nothing; at four it has.
MIN_ATTEMPTS = 4


def record(any_prover: bool = False) -> tuple:
    """Per goal: (times proved, times attempted), plus what was skipped.

    EVERY attempt, not the latest. Latest-wins is what made these look
    saturated -- it reports each goal's best day and hides the failures.
    An `error` row is excluded: it means the harness fell over, which is not
    evidence about the goal either way.

    ATTRIBUTED EVIDENCE ONLY, BY DEFAULT, and this was a real defect.
    ------------------------------------------------------------------
    `run.prover` was only added to the results file on 2026-09-07, so 37 of
    47 files -- 188 goal-runs -- do not say which prover produced them. The
    first version of this script counted them all, and the consequence was
    not cosmetic:

        the self-authored rate split by provenance
            prover recorded (math_v2)   64/70  = 91%
            prover unrecorded           77/96  = 80%
            pooled                     141/166 = 85%   <- what was reported

        8 OF THE 11 RETIREMENTS rested on runs of an unknown prover.

    Retiring a goal is destroying evidence about the CURRENT agent, so it
    must not rest on runs that may have been produced by the baseline one.
    And the old runs cannot be rescued by inference: `stages` appears on 75%
    of known-math_v2 rows and 89% of unattributed ones, so it does not
    discriminate, and `goal_attempts` -- which does, 74% against 5% -- is
    absent from early math_v2 runs too. Ambiguous is ambiguous.

    `any_prover=True` restores the old pooled behaviour for comparison. It
    is not the default because the pooled number is the one that was wrong.
    """
    proved: collections.Counter = collections.Counter()
    attempts: collections.Counter = collections.Counter()
    skipped_files = 0
    skipped_rows = 0
    for path in sorted((ROOT / "eval" / "results").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data["results"]
        except (ValueError, OSError, KeyError, TypeError):
            continue
        prover = (data.get("run") or {}).get("prover")
        usable = [r for r in rows
                  if r.get("goal_id") and r.get("outcome") != "error"]
        if not prover and not any_prover:
            skipped_files += 1
            skipped_rows += len(usable)
            continue
        for row in usable:
            goal_id = row["goal_id"]
            attempts[goal_id] += 1
            if row.get("outcome") == "proved":
                proved[goal_id] += 1
    history = {g: (proved[g], attempts[g]) for g in attempts}
    return history, skipped_files, skipped_rows


def split(goals: list, history: dict, min_attempts: int = MIN_ATTEMPTS):
    """(spent, live, unmeasured) -- three lists, every goal in exactly one.

    A goal with too little history is UNMEASURED and stays live. Silence is
    not evidence of saturation, and retiring on it would drop goals for
    never having been run.
    """
    spent, live, unmeasured = [], [], []
    for goal in goals:
        hit, total = history.get(goal["id"], (0, 0))
        if total < min_attempts:
            unmeasured.append(goal)
        elif hit == total:
            spent.append(goal)
        else:
            live.append(goal)
    return spent, live, unmeasured


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="write eval/proofs-canary.json and "
                             "eval/proofs-live.json. Without it, nothing is "
                             "written and nothing is changed.")
    parser.add_argument("--min-attempts", type=int, default=MIN_ATTEMPTS)
    parser.add_argument(
        "--any-prover", action="store_true",
        help="count runs that do not record which prover produced them. OFF "
             "by default: 8 of 11 retirements once rested on such runs, and "
             "retiring a goal is destroying evidence about the CURRENT "
             "agent. Use it only to reproduce the older pooled figure.")
    parser.add_argument("--goals", type=Path, default=GOALS)
    args = parser.parse_args(argv)

    goals = json.loads(args.goals.read_text(encoding="utf-8"))
    history, skipped_files, skipped_rows = record(args.any_prover)
    spent, live, unmeasured = split(goals, history, args.min_attempts)

    def show(title, rows, note):
        print(f"\n{title}  ({len(rows)})")
        print(f"  {note}")
        for goal in rows:
            hit, total = history.get(goal["id"], (0, 0))
            share = f"{hit}/{total}" if total else "never run"
            print(f"    {goal['id']:28}{goal['tier']:14}{share:>10}")

    print(f"{args.goals.relative_to(ROOT)}: {len(goals)} goals")
    print(f"retiring on a perfect record over >={args.min_attempts} runs")
    if skipped_rows:
        print(f"\nSKIPPED {skipped_rows} goal-runs across {skipped_files} "
              f"results files that do not")
        print("record which prover produced them. `run.prover` was added on")
        print("2026-09-07; earlier files predate it and may be baseline runs.")
        print("Retiring on them would destroy evidence about THIS agent on")
        print("the strength of another one. --any-prover counts them anyway.")
    elif args.any_prover:
        print("\nCOUNTING UNATTRIBUTED RUNS (--any-prover). The pooled figure")
        print("this produces mixes provers and is not about one system.")

    show("SPENT -- retire to the canary file", spent,
         "proved every single time. Cannot show an improvement.")
    show("LIVE -- keep measuring", live,
         "still fails. This is where a change would show up.")
    if unmeasured:
        show("UNMEASURED -- kept, for lack of evidence", unmeasured,
             f"fewer than {args.min_attempts} runs. Silence is not saturation.")

    kept = live + unmeasured
    print(f"\n  {len(spent)} retired, {len(kept)} kept")
    if not args.write:
        print("\nREPORT ONLY. Nothing was written. Add --write to apply.")
        return 0

    CANARY.write_text(json.dumps(spent, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    LIVE.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(f"\nwrote {CANARY.relative_to(ROOT)}  ({len(spent)} goals)")
    print(f"wrote {LIVE.relative_to(ROOT)}  ({len(kept)} goals)")
    print("\neval/proofs.json is UNCHANGED -- the baselines reference it.")
    print("From here on:")
    print(f"  --goals eval/proofs-live.json    the {len(kept)} that still move")
    print(f"  --goals eval/proofs-canary.json  a smoke test, not a measurement")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
