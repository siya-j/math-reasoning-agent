"""Compare two results files goal by goal, and say WHICH guard fired.

An outcome that changed is not by itself evidence that a change worked. This
prints, per goal, the outcome before and after AND whether the mechanisms
shipped for it actually engaged -- read out of the trace, not inferred from
the outcome. A goal that converted without the guard firing converted for some
other reason, and a guard that fired without converting anything is a cost
with no return. Both readings are only available together.

USAGE
    python scripts/compare_runs.py --before eval/results/mixed-1-rebuilt.json \
                                   --after  eval/results/failures-after-decompose.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# THIS DETECTOR HAS A KNOWN BLIND SPOT, and it is not fixable from the
# results file alone. The refusal guards deliberately write NOTHING to the log
# -- "a refusal is not an attempt, so nothing is logged" -- so
# `decompose_first`, `not_the_goal` and `lemma_budget_spent` leave no trace to
# find. The first version of this script looked for them anyway and printed
# "fired: NOTHING" for every goal in a run where the redirect had demonstrably
# worked, which is a detector reporting on markers its producer never emits --
# the same mistake as testing a consumer against a hand-written fixture.
#
# Only the entries below the divider are actually observable. Attribution for
# the refusals has to be inferred from the counts (a goal that suddenly keeps
# lemmas after three failed attempts is the redirect's shape) and inference is
# not observation. A refusal COUNTER in the budget file would close this, and
# until it exists these two groups are printed separately so the difference is
# visible rather than glossed.
SIGNALS = {
    # --- genuinely observable: these write a log note or a trace entry
    "refused the stop": "continuation guard (b5802a8)",
    "continuation failed": "continuation crashed",
    "stopped early": "budget exhausted",
    "agent failed": "crash",
    "refuted statement": "refutation recorded",
    "suspect statement": "statement reported suspect",
}

# Kept for the day a refusal counter exists. Looking for these today is
# guaranteed to find nothing, so they are NOT searched.
UNOBSERVABLE = ("decompose_first", "not_the_goal", "lemma_budget_spent")


def _index(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["goal_id"]: row for row in data.get("results", [])}, data


def _fired(row: dict) -> list:
    """Which mechanisms left a mark on this goal."""
    haystack = "\n".join(list(row.get("trace") or [])
                         + [s.get("errors", "") or "" for s in row.get("stages") or []]
                         + [row.get("detail", "") or ""])
    return [label for marker, label in SIGNALS.items() if marker in haystack]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    args = parser.parse_args()

    before, _ = _index(args.before)
    after, after_data = _index(args.after)

    print(f"before  {args.before}")
    print(f"after   {args.after}  (commit "
          f"{(after_data.get('run') or {}).get('commit', '?')[:8]})")
    print()

    converted, unchanged, regressed = [], [], []
    for goal_id, now in after.items():
        was = before.get(goal_id)
        old = was["outcome"] if was else "(not in before)"
        new = now["outcome"]
        bucket = (converted if new == "proved" and old != "proved"
                  else regressed if old == "proved" and new != "proved"
                  else unchanged)
        bucket.append((goal_id, old, new, now))

    for title, rows in (("CONVERTED", converted),
                        ("REGRESSED", regressed),
                        ("UNCHANGED", unchanged)):
        if not rows:
            continue
        print(f"{title}  ({len(rows)})")
        print("-" * 74)
        for goal_id, old, new, now in sorted(rows):
            print(f"  {goal_id:26} {old:18} -> {new}")
            print(f"     {now['goal_attempts']:>2} goal attempts, "
                  f"{now['lemmas_proved']}/{now['lemmas_total']} lemmas, "
                  f"{now['lean_calls']}/{now['lean_budget']} compiles, "
                  f"{now['model_calls']} model calls")
            fired = _fired(now)
            print(f"     observed: {', '.join(fired) if fired else '(nothing that leaves a record)'}")
        print()

    # Cost, which the new guards are expected to RAISE: refusals cost a model
    # turn each, and the continuation guard re-drives the agent.
    tin = sum(r.get("input_tokens", 0) or 0 for r in after.values())
    calls = sum(r.get("model_calls", 0) or 0 for r in after.values())
    complete = all(r.get("cost_complete", True) for r in after.values())
    print("COST")
    print("-" * 74)
    print(f"  {calls} model calls over {len(after)} goals "
          f"({calls / max(1, len(after)):.1f} per goal)")
    print(f"  {tin:,} input tokens"
          + ("" if complete else "   INCOMPLETE -- some goals returned no transcript"))
    print()
    print("NOTE. A failures-only selection has no meaningful proof RATE -- the")
    print("sample was chosen for having failed. Read per-goal conversion.")
    print()
    print("`observed` covers only mechanisms that leave a record. The refusal")
    print("guards (" + ", ".join(UNOBSERVABLE) + ") write nothing")
    print("by design, so their effect can only be INFERRED from the attempt and")
    print("lemma counts above -- never confirmed from this file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
