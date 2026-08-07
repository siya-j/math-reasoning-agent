"""Measure run-to-run stability.

Temperature is 0, so the same question should give the same verdict every
time. Agent loops can still diverge — tool choice, retry paths, tie-breaks.
If they do, every accuracy number we quote needs an error bar, so this is
worth knowing before trusting any comparison between runs.

    python scripts/variance.py --repeats 3 --limit 8
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import load_cases, run_case  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--area", default="")
    args = parser.parse_args()

    cases = load_cases()
    if args.area:
        cases = [c for c in cases if c.area == args.area]
    cases = cases[: args.limit]

    verdicts = defaultdict(list)
    for run_number in range(1, args.repeats + 1):
        print(f"\n--- run {run_number}/{args.repeats} ---")
        for case in cases:
            result = run_case(case)
            verdicts[case.id].append(result.actual)
            print(f"  {case.id:<28} {result.actual}")

    print("\n" + "=" * 60)
    unstable = 0
    comparable = 0
    incomplete = 0

    for case_id, seen in verdicts.items():
        # An error is a failure to answer, not a different answer. Counting
        # it as verdict instability makes a rate-limited run look
        # non-deterministic when it is nothing of the sort.
        decided = [v for v in seen if v != "error"]
        errors = len(seen) - len(decided)

        if len(decided) < 2:
            incomplete += 1
            print(f"  NO DATA   {case_id:<28} {errors} error(s), too few runs")
            continue

        comparable += 1
        counts = Counter(decided)
        if len(counts) > 1:
            unstable += 1
            spread = ", ".join(f"{v}x{n}" for v, n in counts.items())
            print(f"  UNSTABLE  {case_id:<28} {spread}")
        elif errors:
            print(f"  stable    {case_id:<28} ({errors} error(s) ignored)")

    print("-" * 60)
    print(f"  cases                {len(verdicts)}")
    print(f"  repeats              {args.repeats}")
    print(f"  comparable           {comparable}   (>=2 non-error runs)")
    print(f"  excluded (errors)    {incomplete}")
    print(f"  unstable             {unstable}")
    if comparable:
        print(f"  stability            {(comparable - unstable) / comparable:.0%}")
    else:
        print("  stability            n/a (every case errored)")
    print("=" * 60)

    if unstable:
        print("\nVerdicts vary between identical runs. Treat single-run")
        print("accuracy figures as estimates, not measurements.")
    elif comparable:
        print("\nEvery comparable case gave the same verdict every time.")
        if incomplete:
            print("Some cases had too few successful runs to judge -- rerun")
            print("those on a model without a quota (e.g. ollama).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
