"""Sort real source material, and find out what this architecture is shaped for.

    # costs nothing, shows what it would send
    python3 scripts/triage_sources.py --items eval/sources/example.txt

    # actually sort it
    python3 scripts/triage_sources.py --items eval/sources/mine.txt --run \
        --out eval/results/triage.json

INPUT is deliberately forgiving. Either a JSON list of {id, text, source},
or a plain text file with items separated by a line of dashes:

    Corrected Eq. 14: the prefactor should be 1/(4 pi eps0), not 1/(4 pi).
    ---
    What is the uncertainty on k if I measured t = 2.006 +/- 0.002 s ...
    ---

A format that demands JSON from someone with a PDF open will not get used.

THE NUMBER THIS PRODUCES is not how many cases were harvested. It is what
fraction of real scientific numerical work this system is SHAPED for --
stated claims it can check, versus quantities a researcher wants computed,
for which it would first have to be handed a guess. If that fraction is low,
that is a finding about the architecture and it is worth more than any
number of new benchmark cases.

NOTHING HERE BECOMES A BENCHMARK CASE ON ITS OWN. The output is a draft for
a person to accept or throw away.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval import harvest  # noqa: E402

GIVE_UP_AFTER = 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", required=True,
                        help="source material: JSON, or text split on --- lines")
    parser.add_argument("--run", action="store_true",
                        help="actually call the model (default is a dry run)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    items = harvest.load_items(args.items)
    if args.limit:
        items = items[: args.limit]
    tools = harvest.available_tools()

    print(f"{len(items)} item(s) to sort, against {len(tools)} available checks\n")

    if not args.run:
        print("DRY RUN - nothing sent, nothing spent. Add --run.\n")
        if items:
            print(harvest.PROMPT.format(
                text=items[0].text.strip()[:700],
                tools="\n".join(f"  {name}" for name in tools),
            ))
        if len(items) > 1:
            print(f"\n  ... and {len(items) - 1} more like this.")
        return 0

    import llm

    model = llm.get_model()
    triaged, failures = [], 0

    for index, item in enumerate(items, 1):
        result = harvest.triage(item, model, tools)
        triaged.append(result)

        if "could not be sorted" in result.why:
            failures += 1
            if failures >= GIVE_UP_AFTER:
                print(f"\n{GIVE_UP_AFTER} failures in a row. Stopping: "
                      f"{result.why}")
                break
        else:
            failures = 0

        mark = "USABLE" if result.usable_today else "      "
        print(f"  [{index:3d}/{len(items)}] {mark} {result.kind:17s} "
              f"{result.tool or '-':24s} {result.what[:44]}")

    summary = harvest.summarize(triaged)

    print("\n" + "=" * 70)
    print(f"  sorted                      {summary.total}")
    for kind in harvest.KINDS:
        print(f"    {kind:26s} {summary.counts.get(kind, 0)}")
    print("-" * 70)
    print(f"  SHAPED FOR (stated claims)  {summary.shaped_for:.0%}")
    print("=" * 70)

    if summary.by_tool:
        print("\n  which checks the usable ones need:")
        for tool, count in sorted(summary.by_tool.items(),
                                  key=lambda pair: -pair[1]):
            print(f"    {tool:28s} {count}")

    computations = summary.counts.get(harvest.COMPUTATION, 0)
    if computations > summary.counts.get(harvest.CLAIM, 0):
        print("\n  MORE COMPUTATIONS THAN CLAIMS. The system decides stated")
        print("  claims; this material mostly asks for quantities to be")
        print("  worked out, which it would have to be handed a guess for")
        print("  first. That is a finding about the ARCHITECTURE, and it is")
        print("  worth more than adding cases.")

    out_of_reach = summary.counts.get(harvest.OUT_OF_REACH, 0)
    if out_of_reach:
        print(f"\n  {out_of_reach} item(s) are quantitative and out of reach.")
        print("  Read those before building anything: they are the capability")
        print("  gap, stated by the material rather than guessed at.")

    print("\n  NONE OF THIS IS A BENCHMARK CASE YET. Open them and decide.")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "summary": {
                "total": summary.total,
                "counts": summary.counts,
                "by_tool": summary.by_tool,
                "shaped_for": summary.shaped_for,
            },
            "note": ("A draft for review. Nothing here should become a "
                     "benchmark case without a person reading it: a model "
                     "choosing what the benchmark contains is a model "
                     "deciding what we measure ourselves against."),
            "items": [vars(t) for t in triaged],
        }, indent=2), encoding="utf-8")
        print(f"\nSaved to {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
