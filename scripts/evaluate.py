"""Run the agent against the golden dataset.

    python scripts/evaluate.py                  # everything
    python scripts/evaluate.py --limit 5        # first 5 cases (cheap check)
    python scripts/evaluate.py --area calculus  # one area
    python scripts/evaluate.py --pause 2        # 2s between calls (rate limits)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import load_cases, run_all, save  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="run only the first N cases")
    parser.add_argument("--area", default="", help="filter by area, e.g. calculus")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between cases")
    args = parser.parse_args()

    cases = load_cases()
    if args.area:
        cases = [c for c in cases if c.area == args.area]
    if args.limit:
        cases = cases[: args.limit]

    print(f"Running {len(cases)} case(s)\n")
    results, summary = run_all(cases, pause=args.pause)

    print("\n" + "=" * 52)
    print(f"  total                  {summary['total']}")
    print(f"  correct                {summary['correct']}")
    print(f"  missed (coverage gap)  {summary['missed']}")
    print(f"  WRONG (soundness)      {summary['wrong']}")
    print(f"  errors                 {summary['errors']}")
    print("-" * 52)
    print(f"  accuracy               {summary['accuracy']:.0%}")
    print(f"  soundness              {summary['soundness']:.0%}   <- must be 100%")
    def pct(value):
        return "n/a (no such cases)" if value is None else f"{value:.0%}"

    print(f"  coverage (decidable)   {pct(summary['coverage'])}")
    print(f"  tool use rate          {summary['tool_use_rate']:.0%}")
    print(f"  restraint on abstract  {pct(summary['restraint_on_abstract'])}")
    print(f"  mean checks per case   {summary['mean_checks']:.2f}")
    print(f"  mean attempts per case {summary['mean_attempts']:.2f}")
    print(f"  recovered by retry     {summary['recovered_by_retry']}")
    print("=" * 52)

    if summary["wrong"]:
        print("\nSOUNDNESS FAILURES:")
        for result in results:
            if result.outcome.value == "wrong":
                print(f"  {result.case_id}: expected {result.expected}, got {result.actual}")
                print(f"    {result.detail}")

    path = save(results, summary)
    print(f"\nSaved to {path}")
    return 1 if summary["wrong"] or summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
