"""Stratified sample across ProofNet chapters — zero cost, no model calls.

Why this exists: `eval/proofnet.py`'s `area_of()` groups goals by textbook
chapter (`exercise_1_13a` -> `proofnet 1`), and that grouping is the only
difficulty-adjacent signal ProofNet ships (it has no difficulty field; arXiv
2406.07222 doesn't add one either). But a small ProofNet sample can easily
collapse onto one chapter without you noticing — the existing
`eval/proofnet-4.json` fixture is four goals and all four are `proofnet 1`,
so a per-chapter table computed from it would show exactly one row. This
script picks at most N goals per chapter out of a full goals file so a
chapter-breakdown experiment has something to break down, while bounding
API cost far below running every chapter's full goal count.

    python -m eval.proofnet --dataset v3 --split validation --mode formal \\
        --out eval/proofnet-v3-validation.json
    python scripts/sample_by_area.py eval/proofnet-v3-validation.json \\
        --per-area 2 --out eval/proofnet-by-chapter-sample.json

Both steps are free — downloading/converting and sampling touch no model and
no Lean. Only the eventual `evaluate_proofs.py --goals
eval/proofnet-by-chapter-sample.json` run costs anything, and it costs
`len(sample)` goals, not 182.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def sample(goals: list[dict], per_area: int) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for g in goals:
        buckets[g["area"]].append(g)

    picked = []
    for area in sorted(buckets):
        picked.extend(buckets[area][:per_area])
    return picked, buckets


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goals_file", help="a goals file produced by eval/proofnet.py")
    parser.add_argument(
        "--per-area", type=int, default=2,
        help="max goals to keep per chapter/area (default 2)",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    goals = json.loads(Path(args.goals_file).read_text(encoding="utf-8"))
    picked, buckets = sample(goals, args.per_area)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(picked, indent=2, ensure_ascii=False), encoding="utf-8")

    sizes = ", ".join(f"{a.split()[-1]}:{len(v)}" for a, v in sorted(buckets.items()))
    print(f"{len(buckets)} chapters found in {args.goals_file} ({sizes})")
    print(f"sampled {len(picked)} goals (up to {args.per_area} per chapter) -> {out}")
    if len(buckets) == 1:
        print("\nWARNING: only one chapter present. A per-area breakdown over "
              "this file will show a single row, same as eval/proofnet-4.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
