"""Combine goal files into one mixed benchmark, reporting what came from where.

    python scripts/mixed_benchmark.py \
        --curated \
        --add eval/proofnet-by-subject-sample.json:20 \
        --add eval/putnam-sample-100.json:20 \
        --out eval/mixed-benchmark.json

WHY A MIXED SET BEATS A SINGLE SOURCE
-------------------------------------
`eval/proof_metrics.summarize` has always emitted a per-tier proof rate, and
it has NEVER been populated: every run in this project's history drew from one
source, so a report reads

    in-mathlib   n/a (no such cases)
    near-mathlib n/a (no such cases)
    ...
    putnam       40%

One number says how good the agent is. A gradient across tiers says WHERE it
works, which is both more useful and much harder to argue with.

MEASURED, aggregated over every results file in eval/results/ -- and this is
the reason a mixed set matters more than a bigger single one:

    tier          calls/goal   proved     source
    in-mathlib             8   6/6  100%  written by this project
    near-mathlib           6  52/57  91%  written by this project
    deep                  13   4/5   80%  written by this project
    hard                  17  14/26  54%  written by this project
    putnam                26   4/17  24%  EXTERNAL
    proofnet              13   7/75   9%  EXTERNAL

The tiers this agent excels at are the ones it was developed against. 100% and
91% on self-authored goals beside 9% on external ProofNet is what tuning to
your own benchmark looks like, and a reader will see it immediately. Publishing
the gradient WITH its sources is the honest version and the stronger one; a
single flattering rate invites exactly the question this table answers.

It is also cheaper. Using calls/goal above, 25 curated + 20 ProofNet + 20
Putnam is roughly 1,040 model calls, against 3,220 for 124 Putnam goals --
a third of the cost, every tier populated.

WHAT THIS SCRIPT DOES NOT DO. It does not sample: pre-sample each source with
`scripts/sample_by_area.py`, which takes a seed and records it. This only
concatenates, deduplicates by id, and prints the composition, so the mix is
reproducible from the command line that made it.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.proof_dataset import load_goals  # noqa: E402


def curated() -> list[dict]:
    """The 25-goal curated benchmark, as plain dicts.

    Kept whole rather than sampled: at 25 goals across five tiers it is
    already small, and dropping any of them empties a tier row -- `novel` has
    only two goals and `in-mathlib` six.
    """
    return [
        {"id": g.id, "area": g.area, "goal": g.goal, "tier": g.tier.value,
         **({"mathlib": g.mathlib} if g.mathlib else {}),
         **({"note": g.note} if g.note else {})}
        for g in load_goals()
    ]


def take(path: str, count: int) -> list[dict]:
    goals = json.loads(Path(path).read_text(encoding="utf-8"))
    if count and count < len(goals):
        # The FIRST n of an already-sampled file, so the seed that produced
        # the sample is what decides membership. Re-shuffling here would make
        # the mix depend on two seeds, only one of which gets recorded.
        goals = goals[:count]
    return goals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--curated", action="store_true",
                        help="include the 25-goal curated benchmark whole")
    parser.add_argument("--add", action="append", default=[], metavar="FILE:N",
                        help="take the first N goals of a goals file")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    mixed: list[dict] = []
    sources: list[tuple[str, int]] = []

    if args.curated:
        picked = curated()
        mixed += picked
        sources.append(("curated (eval/proof_dataset)", len(picked)))

    for spec in args.add:
        path, _, raw = spec.rpartition(":")
        if not path:
            path, raw = spec, "0"
        picked = take(path, int(raw or 0))
        mixed += picked
        sources.append((path, len(picked)))

    if not mixed:
        print("nothing selected; pass --curated and/or --add FILE:N")
        return 2

    # Deduplicate by id, keeping the first occurrence, so overlapping sources
    # cannot inflate a tier's denominator.
    seen: set[str] = set()
    unique = []
    duplicates = 0
    for goal in mixed:
        gid = goal.get("id", "")
        if gid in seen:
            duplicates += 1
            continue
        seen.add(gid)
        unique.append(goal)

    args.out.write_text(json.dumps(unique, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"{len(unique)} goals -> {args.out}")
    if duplicates:
        print(f"  ({duplicates} duplicate id(s) dropped)")
    print("\nby source:")
    for path, count in sources:
        print(f"  {count:>4d}  {path}")
    print("\nby tier — every row here becomes a line in the run's report:")
    for tier, count in sorted(collections.Counter(
            g.get("tier", "?") for g in unique).items()):
        print(f"  {count:>4d}  {tier}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
