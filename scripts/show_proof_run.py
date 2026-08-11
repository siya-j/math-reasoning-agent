"""Read back a saved proof run — every stage, every error, in full.

    python scripts/show_proof_run.py
    python scripts/show_proof_run.py --goal grp-prime-order-cyclic

The terminal output of a run is a summary. This is what actually happened:
the formal statement, each attempt's source, and the compiler's objection to
it. Diagnosing a failure from the summary alone means guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT = Path(__file__).parent.parent / "eval" / "last_proof_run.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default=str(DEFAULT))
    parser.add_argument("--goal", help="show only this goal id")
    parser.add_argument(
        "--full", action="store_true", help="do not truncate proofs or errors"
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"No run at {path}")
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    limit = 10_000 if args.full else 400

    for result in data.get("results", []):
        if args.goal and result.get("goal_id") != args.goal:
            continue

        print("=" * 70)
        print(f"{result.get('goal_id')}   [{result.get('tier')}]   "
              f"-> {result.get('outcome')}")
        print("=" * 70)
        print(f"\nformal statement:\n  {result.get('statement') or '(none)'}\n")

        trace = result.get("trace") or []
        if trace:
            print("trace:")
            for entry in trace:
                print(f"  {entry[:limit]}")
            print()

        for index, stage in enumerate(result.get("stages") or [], start=1):
            print(f"--- attempt {index}: {stage.get('stage')}")
            print("    proof:")
            for line in (stage.get("proof") or "")[:limit].splitlines():
                print(f"      {line}")
            print("    compiler said:")
            for line in (stage.get("errors") or "")[:limit].splitlines():
                print(f"      {line}")
            print()

        if not result.get("stages"):
            print(f"detail: {result.get('detail', '')[:limit]}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
