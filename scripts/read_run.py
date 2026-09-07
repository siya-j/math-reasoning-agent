"""Read a results file the way this project's open questions demand.

    python scripts/read_run.py eval/results/mixed-1.json
    python scripts/read_run.py eval/results/mixed-1.json --against eval/results/putnam-run4.json

WHY A SCRIPT AND NOT A DOZEN AD-HOC QUERIES
-------------------------------------------
`evaluate_proofs.py` prints a summary, and every important question asked of a
run in this project has needed something the summary does not show: the
outcome MIX inside a tier, how often a goal walked away rather than being
beaten, whether a rate is self-graded or external, and what each tier cost.
Those got answered by hand each time, differently each time, which is how
`lemma_yield` sat at a structurally-impossible 0.0 for the project's whole
history without anyone noticing.

THE QUESTIONS THIS ANSWERS, fixed in advance so the reading is not improvised:

  1. the tier gradient, with SOURCE marked -- the curated tiers were written
     by this project and the external ones were not, and 100% on the former
     beside 9% on the latter is the first thing a reader will ask about
  2. the outcome mix per tier -- MEASURED on ProofNet: 18 of 75 statements
     never elaborated and 17 more were flagged suspect, so 47% of its failures
     happen before proving starts and a bare "9% proof rate" describes
     something else entirely
  3. exhaustion -- 23 of 40 valid ProofNet targets ran out of budget while the
     agent could not read its own compiler errors; that is the number today's
     feedback fix should move most
  4. walked away vs beaten -- an unproved goal that spent a fifth of its
     compiles chose to stop
  5. cost per tier, and whether context trimming engaged at all -- it never
     fired on the curated goals, whose peak context is under the trigger
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

# Which tiers this project authored. The distinction is the single most
# important thing about a proof rate here and appears nowhere in the data.
SELF_AUTHORED = {"in-mathlib", "near-mathlib", "novel", "hard", "deep"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def gradient(results: list[dict]) -> None:
    print("\nTIER GRADIENT" + " " * 24 + "(source matters: see note below)")
    print("-" * 78)
    print(f"  {'tier':14s} {'n':>3s} {'proved':>7s} {'rate':>6s} "
          f"{'exhaust':>8s} {'walked':>7s} {'in tok/goal':>12s}  source")
    by_tier: dict[str, list[dict]] = collections.defaultdict(list)
    for r in results:
        by_tier[r.get("tier", "?")].append(r)

    for tier, rows in sorted(by_tier.items()):
        counted = [r for r in rows if r.get("outcome") != "error"]
        if not counted:
            continue
        proved = [r for r in counted if r.get("outcome") == "proved"]
        exhausted = [r for r in counted if r.get("outcome") == "exhausted"]
        walked = [r for r in counted
                  if r.get("outcome") != "proved" and r.get("lean_budget")
                  and r.get("lean_calls", 0) < 0.5 * r["lean_budget"]]
        tok = sum(r.get("input_tokens", 0) for r in counted) // max(1, len(counted))
        origin = "self-authored" if tier in SELF_AUTHORED else "EXTERNAL"
        print(f"  {tier:14s} {len(counted):>3d} {len(proved):>7d} "
              f"{len(proved)/len(counted):>5.0%} {len(exhausted):>8d} "
              f"{len(walked):>7d} {tok:>12,}  {origin}")


def outcome_mix(results: list[dict]) -> None:
    print("\nOUTCOME MIX PER TIER — a proof rate hides what the failures were")
    print("-" * 78)
    by_tier: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    for r in results:
        by_tier[r.get("tier", "?")][r.get("outcome", "?")] += 1
    for tier, counts in sorted(by_tier.items()):
        total = sum(counts.values())
        before_proving = counts["not_formalized"] + counts["suspect_statement"]
        parts = "  ".join(f"{k}={v}" for k, v in counts.most_common())
        print(f"  {tier:14s} {parts}")
        if before_proving:
            print(f"  {'':14s} -> {before_proving}/{total} "
                  f"({before_proving/total:.0%}) failed BEFORE proving began")


def cost(data: dict, results: list[dict]) -> None:
    print("\nCOST")
    print("-" * 78)
    trigger = data.get("environment", {}).get("context_trim_trigger")
    counted = [r for r in results if r.get("outcome") != "error"]
    gin = sum(r.get("input_tokens", 0) for r in counted)
    gout = sum(r.get("output_tokens", 0) for r in counted)
    calls = sum(r.get("model_calls", 0) for r in counted)
    print(f"  input {gin:>14,}   output {gout:>12,}   over {len(counted)} goals")
    if calls:
        per = gin // calls
        print(f"  {per:,} input per model call, across {calls} calls")
        if trigger:
            reached = sum(1 for r in counted
                          if r.get("model_calls") and
                          2 * r["input_tokens"] // r["model_calls"] > trigger)
            print(f"  trimming trigger {trigger:,}: peak context plausibly "
                  f"reached it on {reached} of {len(counted)} goals")
            if not reached:
                print("  -> trimming never engaged; it cannot have saved anything")
    incomplete = [r for r in counted if not r.get("cost_complete", True)]
    if incomplete:
        print(f"  WARNING: {len(incomplete)} goal(s) returned no transcript, so "
              "these totals are a FLOOR")


def compare(new: dict, old: dict) -> None:
    print("\nAGAINST THE EARLIER RUN — only shared goal ids, so it is like for like")
    print("-" * 78)
    on = {r["goal_id"]: r for r in old.get("results", [])}
    shared = [r for r in new.get("results", []) if r["goal_id"] in on]
    if not shared:
        print("  no goals in common")
        return
    for r in shared:
        o = on[r["goal_id"]]
        arrow = "->"
        print(f"  {r['goal_id']:22s} {o.get('outcome',''):11s} {arrow} "
              f"{r.get('outcome',''):11s}  "
              f"in {o.get('input_tokens',0):>9,} {arrow} {r.get('input_tokens',0):>9,}")
    a = sum(on[r["goal_id"]].get("input_tokens", 0) for r in shared)
    b = sum(r.get("input_tokens", 0) for r in shared)
    if a:
        # `(b - a) / a`, so a REDUCTION prints negative. The obvious
        # `1 - b/a` is the saving and displays a 50% cut as "+50%", which
        # reads as a 50% increase to anyone who has not read this line.
        print(f"\n  shared-goal input: {a:,} {arrow} {b:,}  ({(b-a)/a:+.0%})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("--against", type=Path,
                        help="an earlier results file, compared on shared ids only")
    args = parser.parse_args(argv)

    data = load(args.results)
    results = data.get("results", [])
    run = data.get("run", {})

    print("=" * 78)
    print(f"{args.results}")
    if run:
        print(f"  model {run.get('model','?')}   profile "
              f"{run.get('budget_profile') or 'none'}   commit "
              f"{(run.get('commit') or '?')[:8]}")
        print(f"  goals {run.get('goals_file','?')}")
    else:
        print("  (no `run` block: this file predates provenance recording)")
    print("=" * 78)

    gradient(results)
    outcome_mix(results)
    cost(data, results)
    if args.against and args.against.exists():
        compare(data, load(args.against))

    print("\nNOTE. The self-authored tiers were written by this project and the "
          "\nEXTERNAL ones were not. Any headline rate mixing them is not one "
          "\nnumber but two, and the gap between them is the finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
