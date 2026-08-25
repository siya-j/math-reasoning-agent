"""Stratified sample across ProofNet chapters or subjects — zero cost, no model calls.

Why this exists: `eval/proofnet.py`'s `area_of()` groups goals by textbook
chapter (`exercise_1_13a` -> `proofnet 1`), and that grouping is the only
difficulty-adjacent signal ProofNet ships (it has no difficulty field; arXiv
2406.07222 doesn't add one either — checked directly against the raw dataset:
the only fields a row carries are `name`, `informal_prefix`,
`formal_statement`, `goal`, `header`, `nl_statement`; nothing names a subject
or a difficulty). But a small ProofNet sample can easily collapse onto one
chapter without you noticing — the existing `eval/proofnet-4.json` fixture is
four goals and all four are `proofnet 1`, so a per-chapter table computed
from it would show exactly one row. This script picks at most N goals per
bucket out of a full goals file so a breakdown experiment has something to
break down, while bounding API cost far below running every bucket's full
goal count.

    python -m eval.proofnet --dataset v3 --split validation --mode formal \\
        --out eval/proofnet-v3-validation.json
    python scripts/sample_by_area.py eval/proofnet-v3-validation.json \\
        --per-area 2 --out eval/proofnet-by-chapter-sample.json

Both steps are free — downloading/converting and sampling touch no model and
no Lean. Only the eventual `evaluate_proofs.py --goals
eval/proofnet-by-chapter-sample.json` run costs anything, and it costs
`len(sample)` goals, not 182.

MEASURED, and the reason this file has two safeguards it did not have at
first: a pilot `--per-area 2` sample landed `exercise_1_13a` and
`exercise_1_13c` — two of the four goals in `eval/proofnet-4.json`, the
fixture this project has tuned against throughout its history. Root cause:
this script took the first N goals per chapter IN SOURCE ORDER, with no
shuffling, and the dataset's row order for chapter 1 begins with exactly the
four goals `proofnet-4.json` was built from, in the same order. Any
`--per-area >= 1` run would have re-included some or all of them, every
time — the "these are goals neither of us has seen" property the sample
exists for was broken by construction for at least that chapter.

Two independent fixes, deliberately not just one: a seeded shuffle makes ANY
row-order coincidence like this one unlikely to repeat, for chapters we have
not specifically checked; excluding the four known ids by name closes the
one instance we know about with certainty rather than leaving it to chance.

CHAPTER STRATIFICATION DOES NOT MEAN SUBJECT BALANCE
-----------------------------------------------------
MEASURED on a real `--per-area 2` sample: 35 chapters, but group theory and
topology alone were 62% of the 52 goals (16 + 16), with number theory and set
theory essentially absent. "Spread across many chapters" and "even coverage
of real math subjects" are different properties, and this dataset only ever
promised the first one.

`--stratify-by subject` addresses the second, by a HEURISTIC keyword
classifier (`SUBJECT_BUCKETS` below) run over each goal's own statement text
— not a verified label, because none exists. It will misfile some goals
(ambiguous statements, or ones matching no keyword land in "unclassified").
Treat "subject" as a best-effort grouping for getting COVERAGE across real
math areas, not as ground truth to report as if a human tagged it.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

# Fixed, not left to `--seed`'s default drifting between runs: a sample two
# people generate separately should be the same sample, unless a different
# seed is asked for on purpose.
DEFAULT_SEED = 20260101

KNOWN_TUNED_AGAINST = Path(__file__).parent.parent / "eval" / "proofnet-4.json"

# Ordered: the first pattern that matches wins, so put the most specific
# checks first (e.g. `Complex` before a bare `Differentiable` would mislabel
# complex analysis as real analysis).
SUBJECT_BUCKETS = (
    ("group theory",             r"\bGroup\b|orderOf|Subgroup|IsCyclic|Normal\b"),
    ("ring / ideal theory",      r"\bIdeal\b|\bRing\b|Polynomial|PrincipalIdeal|Noetherian"),
    ("linear algebra",           r"Matrix|LinearMap|Basis|Vector[Ss]pace|Module\b|eigen"),
    ("complex analysis",         r"ℂ|Complex|holomorphic|Differentiable.*ℂ"),
    ("real analysis",            r"MeasureTheory|Continuous|deriv |Differentiable(?!.*ℂ)|Metric|Tendsto"),
    ("topology",                 r"IsOpen|IsCompact|IsConnected|TopologicalSpace"),
    ("set theory / cardinality", r"Countable|Cardinal|Set\.range"),
    ("number theory",            r"Nat\.Prime|Int\.gcd|Nat\.gcd|ZMod|\bOdd\b|\bEven\b|"
                                  r"GaussianInt|\.Prime\b|∣|gcd"),
)

_PUTNAM = re.compile(r"exercise_(19|20)\d{2}_")


def subject_of(goal: dict) -> str:
    """Best-effort subject label, from the goal's own text. NOT a verified tag.

    See the module docstring's "CHAPTER STRATIFICATION..." section for why
    this exists and what its accuracy actually is.
    """
    if _PUTNAM.match(goal.get("id", "")):
        return "putnam (competition, not textbook)"
    text = goal.get("goal", "")
    for label, pattern in SUBJECT_BUCKETS:
        if re.search(pattern, text):
            return label
    return "unclassified"


def _known_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {g["id"] for g in json.loads(path.read_text(encoding="utf-8"))}


def sample(goals: list[dict], per_area: int, seed: int = DEFAULT_SEED,
          exclude: set[str] = frozenset(),
          stratify_by: str = "chapter") -> tuple[list[dict], dict[str, list[dict]]]:
    key = (lambda g: g["area"]) if stratify_by == "chapter" else subject_of
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for g in goals:
        if g["id"] in exclude:
            continue
        buckets[key(g)].append(g)

    picked = []
    for bucket_key in sorted(buckets):
        bucket = list(buckets[bucket_key])
        rng.shuffle(bucket)          # do not let source row order pick itself
        picked.extend(bucket[:per_area])
    return picked, buckets


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goals_file", help="a goals file produced by eval/proofnet.py")
    parser.add_argument(
        "--per-area", type=int, default=2,
        help="max goals to keep per bucket (default 2)",
    )
    parser.add_argument(
        "--stratify-by", choices=["chapter", "subject"], default="chapter",
        help="'chapter' (default) uses area_of()'s textbook-chapter number, "
        "same as always. 'subject' uses the heuristic classifier above for "
        "even coverage of real math areas instead of chapter spread -- read "
        "the module docstring before trusting it as ground truth.",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help=f"shuffle seed, so the sample is reproducible (default {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--include-known", action="store_true",
        help="do not exclude the ids in eval/proofnet-4.json (off by default: "
        "those goals have been tuned against throughout this project and are "
        "not held-out data)",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    goals = json.loads(Path(args.goals_file).read_text(encoding="utf-8"))
    exclude = set() if args.include_known else _known_ids(KNOWN_TUNED_AGAINST)
    picked, buckets = sample(goals, args.per_area, args.seed, exclude, args.stratify_by)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(picked, indent=2, ensure_ascii=False), encoding="utf-8")

    noun = "chapters" if args.stratify_by == "chapter" else "subject buckets"
    sizes = ", ".join(f"{a.split()[-1] if args.stratify_by == 'chapter' else a}:{len(v)}"
                      for a, v in sorted(buckets.items()))
    print(f"{len(buckets)} {noun} found in {args.goals_file} ({sizes})")
    if exclude:
        print(f"excluded {len(exclude)} id(s) already tuned against "
              f"({KNOWN_TUNED_AGAINST.name}); pass --include-known to keep them")
    print(f"sampled {len(picked)} goals (up to {args.per_area} per {args.stratify_by}, "
          f"seed={args.seed}) -> {out}")
    if len(buckets) == 1:
        print(f"\nWARNING: only one {args.stratify_by} present. A per-area "
              "breakdown over this file will show a single row, same as "
              "eval/proofnet-4.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
