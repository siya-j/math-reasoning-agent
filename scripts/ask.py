"""Ask the agent anything. The single entry point.

    python scripts/ask.py "Is 561 a prime number?"
    python scripts/ask.py "Every group of prime order is cyclic"

Classifies the question, routes it to SymPy or Lean, and falls back to the
other engine if the first settles nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.router import ask  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="a mathematical question, in English")
    parser.add_argument("--trace", action="store_true", help="show routing decisions")
    args = parser.parse_args()

    try:
        answer = ask(args.question)
    except Exception as exc:
        print(f"Could not run: {exc}", file=sys.stderr)
        return 2

    print(answer.report())
    if args.trace:
        print("\nrouting trace:")
        for entry in answer.trace:
            print(f"  {entry}")
    return 0 if answer.settled else 1


if __name__ == "__main__":
    raise SystemExit(main())
