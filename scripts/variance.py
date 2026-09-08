"""How much does the same goal move between runs? Measured, not assumed.

WHY IT MATTERS MORE THAN ANY SINGLE RATE

Every figure this project quotes is one sample. That is fine while the numbers
are being used to find bugs, and not fine the moment they are used to claim a
capability -- a 15-goal result carries a 95% interval of roughly 38-88% around
10/15, which cannot distinguish 67% from 85%.

MEASURED, and this is what prompted the script: `exercise_3_22` compiled a
refutation of Baire's theorem on one run and reported only unverified
suspicion on the next two. Same goal, three runs, two different answers -- and
that refutation is the strongest single result the project has produced.

WHAT THIS DOES. Reads every results file, finds goals decided more than once,
and reports how often the outcome agreed. It needs no runs of its own: the
repeats already exist because goals get re-run as code changes. That makes the
measurement CONFOUNDED -- the code differed between those runs, so what is
reported is run-to-run variation, not sampling noise at a fixed commit. It is
a floor on the noise, not an estimate of it, and the script says so.

For an unconfounded number, run the same goals repeatedly at one commit:

    for i in 1 2 3; do
      python scripts/evaluate_proofs.py --goals eval/proofnet-182.json \\
        --tier proofnet --limit 20 --shuffle --seed $i \\
        --out eval/results/variance-$i.json
    done
    python scripts/variance.py eval/results/variance-*.json
"""

from __future__ import annotations

import argparse
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

# Outcomes that mean the same thing for a stability count. `refuted` and
# `suspect_statement` are NOT collapsed: one is a compiler fact and the other
# an unverified report, and the gap between them is exactly the instability
# that prompted this script.
DECIDED = ("proved", "not_proved", "refuted", "suspect_statement",
           "not_formalized", "exhausted")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="*",
                        default=sorted(glob.glob("eval/results/*.json")))
    args = parser.parse_args(argv)

    seen = defaultdict(list)
    for name in args.files:
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        commit = (data.get("run") or {}).get("commit", "")[:8] or "?"
        for row in data.get("results", []):
            if row.get("outcome") in DECIDED:
                seen[row["goal_id"]].append(
                    (Path(name).stem, commit, row["outcome"]))

    repeated = {g: rs for g, rs in seen.items() if len(rs) > 1}
    print(f"goals decided at least once: {len(seen)}")
    print(f"goals decided more than once: {len(repeated)}")
    if not repeated:
        print("\nNo goal has been decided twice, so nothing can be said about "
              "stability yet.")
        return 1

    stable = [g for g, rs in repeated.items()
              if len({o for _, _, o in rs}) == 1]
    unstable = {g: rs for g, rs in repeated.items()
                if len({o for _, _, o in rs}) > 1}

    print(f"  same outcome every time: {len(stable)}")
    print(f"  changed outcome at least once: {len(unstable)}")
    print(f"  -> agreement {len(stable) / len(repeated) * 100:.0f}%")

    if unstable:
        print()
        print("GOALS THAT MOVED")
        print("-" * 70)
        for goal_id, runs in sorted(unstable.items()):
            print(f"  {goal_id}")
            for name, commit, outcome in runs:
                print(f"      {outcome:18} {commit}  {name}")

    print()
    print("HOW MANY REPEATS EACH GOAL HAS")
    print("-" * 70)
    for count, n in sorted(Counter(len(rs) for rs in repeated.values()).items()):
        print(f"  {count} runs: {n} goal(s)")

    print()
    print("CONFOUNDED, DELIBERATELY REPORTED AS SUCH. These repeats come from")
    print("re-running goals as the code changed, so the commit differs between")
    print("them and this mixes sampling noise with real behaviour change. It is")
    print("a FLOOR on the noise, not an estimate. For a clean number, run the")
    print("same goals several times at ONE commit -- see this file's docstring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
