"""Is that difference real, or is it the run-to-run noise?

    # how noisy is the instrument?
    python3 scripts/significance.py eval/results/test-186.json \
                                    eval/results/test-186-retry.json

    # did a change help?  (A is the new thing, B is the baseline)
    python3 scripts/significance.py --a eval/results/trim-on.json \
                                    --b eval/results/trim-off.json

    # several runs a side
    python3 scripts/significance.py --a run1.json run2.json \
                                    --b base1.json base2.json

With plain positional files it reports the NOISE: how much the same
configuration disagrees with itself. With --a and --b it reports a PAIRED
comparison, which is the only kind worth making when the same goals appear
on both sides.

Distinct from scripts/compare_runs.py, which answers a different question:
that one says WHICH MECHANISM fired on a goal, this one says whether a
difference in the totals means anything.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import variance  # noqa: E402


def _load(paths: list[str]) -> list[variance.Run]:
    return [variance.load_run(path) for path in paths]


def _percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def report_noise(runs: list[variance.Run]) -> None:
    goals = variance.common_goals(runs)
    print(f"  runs compared          {len(runs)}")
    for run in runs:
        print(f"    {run.name:38s} {len(run.valid):4d} valid goals")
    print(f"  goals valid in all     {len(goals)}")
    if not goals:
        print("\n  Nothing in common. These runs cannot be compared.")
        return

    print()
    for run in runs:
        print(f"    {run.name:38s} {_percent(variance.rate(run, goals))}")

    interval = variance.rate_interval(runs, goals)
    unstable = variance.flippers(runs, goals)

    print()
    print(f"  single-run rate        {_percent(interval.point)} "
          f"(95% CI {_percent(interval.low)} to {_percent(interval.high)})")
    print(f"  proved by at least one {_percent(variance.pass_at_k(runs, goals))}")
    print(f"  goals that flipped     {len(unstable)} of {len(goals)}"
          f"  ({_percent(len(unstable) / len(goals))})")

    if unstable:
        print()
        print("  THE FLIP RATE IS THE INSTRUMENT'S NOISE FLOOR. A change that")
        print("  moves the rate by less than this is not measurable with one")
        print("  run a side, however plausible it sounds.")
        shown = unstable[:10]
        print(f"\n  flipped: {', '.join(shown)}"
              + (f", and {len(unstable) - len(shown)} more"
                 if len(unstable) > len(shown) else ""))


def report_comparison(
    group_a: list[variance.Run], group_b: list[variance.Run]
) -> None:
    goals = variance.common_goals(group_a + group_b)
    print(f"  A: {len(group_a)} run(s)  {', '.join(r.name for r in group_a)}")
    print(f"  B: {len(group_b)} run(s)  {', '.join(r.name for r in group_b)}")
    print(f"  goals valid in all     {len(goals)}")
    if not goals:
        print("\n  Nothing in common. These runs cannot be compared.")
        return

    rate_a = variance.rate_interval(group_a, goals)
    rate_b = variance.rate_interval(group_b, goals)
    print()
    print(f"  A rate                 {_percent(rate_a.point)}")
    print(f"  B rate                 {_percent(rate_b.point)}")

    if len(group_a) == 1 and len(group_b) == 1:
        result = variance.mcnemar(group_a[0], group_b[0], goals)
        print()
        print("  paired, exact McNemar (only disagreeing goals inform it)")
        print(f"    proved by A only     {result.only_a}")
        print(f"    proved by B only     {result.only_b}")
        print(f"    proved by both       {result.both}")
        print(f"    proved by neither    {result.neither}")
        print(f"    p-value              {result.p_value:.4f}")
        print()
        verdict = ("DISTINGUISHABLE from noise"
                   if result.distinguishable else
                   "NOT distinguishable from noise")
        print(f"  VERDICT: {verdict}")
        print()
        print("  " + variance.detectable_difference(result.discordant))
        if not result.distinguishable:
            print()
            print("  This is not evidence that the change did nothing. It is")
            print("  the absence of evidence that it did something, which on")
            print("  one run a side is the usual outcome for any real but")
            print("  modest effect.")
        return

    difference = variance.paired_difference(group_a, group_b, goals)
    print()
    print("  paired bootstrap over goals")
    print(f"    mean A - B           {_percent(difference.point)}")
    print(f"    95% interval         {_percent(difference.low)} to "
          f"{_percent(difference.high)}")
    print()
    if difference.includes_zero:
        print("  VERDICT: NOT distinguishable from noise "
              "(the interval contains zero)")
        print()
        print("  This is not evidence that the change did nothing. It is the")
        print("  absence of evidence that it did something.")
    else:
        direction = "better" if difference.point > 0 else "worse"
        print(f"  VERDICT: A is {direction} than B, and the interval "
              "excludes zero")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="*", help="results files, to measure noise")
    parser.add_argument("--a", nargs="+", default=[], help="the new configuration")
    parser.add_argument("--b", nargs="+", default=[], help="the baseline")
    args = parser.parse_args()

    if bool(args.a) != bool(args.b):
        parser.error("--a and --b must be given together")

    print("=" * 66)
    if args.a:
        print("  PAIRED COMPARISON")
        print("=" * 66)
        report_comparison(_load(args.a), _load(args.b))
    else:
        if len(args.runs) < 2:
            parser.error("give at least two results files, or use --a and --b")
        print("  RUN-TO-RUN NOISE")
        print("=" * 66)
        report_noise(_load(args.runs))
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
