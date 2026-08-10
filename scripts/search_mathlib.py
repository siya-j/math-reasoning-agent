"""Search Mathlib for declarations. Needs no Lean and no API key.

    python scripts/search_mathlib.py "Nat.Prime"
    python scripts/search_mathlib.py "IsCyclic" --limit 10
    python scripts/search_mathlib.py --statement "theorem t [Group G] : IsCyclic G"

--statement runs the same extraction the prover uses, so you can see exactly
which premises the model would be shown for a given theorem.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval.loogle import LoogleSearch, extract_queries  # noqa: E402
import config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="a Mathlib name or type pattern")
    parser.add_argument(
        "--statement", help="a Lean theorem; searches every identifier in it"
    )
    parser.add_argument("--limit", type=int, default=config.PREMISES_PER_QUERY)
    args = parser.parse_args()

    if not args.query and not args.statement:
        parser.error("give a query or --statement")

    search = LoogleSearch()

    if args.statement:
        queries = extract_queries(args.statement, config.MAX_PREMISE_QUERIES)
        print(f"identifiers found: {', '.join(queries) or '(none)'}\n")
        premises = search.premises_for(args.statement)
    else:
        premises = search.search(args.query, limit=args.limit)

    if not premises:
        print("No results. (Loogle may be unreachable — retrieval fails quietly.)")
        return 1

    for premise in premises:
        print(f"{premise.name}{premise.type}")
        print(f"    {premise.module}")
    print(f"\n{len(premises)} declaration(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
