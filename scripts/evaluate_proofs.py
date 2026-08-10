"""Run the prover over the goal set and report four separate numbers.

    python scripts/evaluate_proofs.py --tier in-mathlib
    python scripts/evaluate_proofs.py --limit 3 --depth 0

Needs a model and Lean. Budget carefully: at LEMMA_DEPTH=1 a single goal
costs roughly 28 model calls and five Lean invocations.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from eval.proof_dataset import Tier, load_goals  # noqa: E402
from eval.proof_metrics import (  # noqa: E402
    ProofOutcome,
    ProofResult,
    render,
    result_from,
    summarize,
)
from pipeline.prover import prove  # noqa: E402
from verifiers.lean_runner import lean_is_available  # noqa: E402

OUT = Path(__file__).parent.parent / "eval" / "last_proof_run.json"
CONSECUTIVE_ERROR_LIMIT = 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=[t.value for t in Tier])
    parser.add_argument("--area")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--depth", type=int, default=None, help="override config.LEMMA_DEPTH"
    )
    args = parser.parse_args()

    goals = load_goals()
    if args.tier:
        goals = [g for g in goals if g.tier.value == args.tier]
    if args.area:
        goals = [g for g in goals if g.area == args.area]
    if args.limit:
        goals = goals[: args.limit]

    if not goals:
        print("No goals matched.")
        return 2

    if not lean_is_available():
        print("WARNING: Lean not found. Every goal will be NOT PROVED.\n")

    print(f"model: {config.MODEL}")
    print(f"goals: {len(goals)}  depth: {args.depth if args.depth is not None else config.LEMMA_DEPTH}\n")

    results: list[ProofResult] = []
    consecutive_errors = 0

    for index, goal in enumerate(goals, start=1):
        print(f"[{index}/{len(goals)}] {goal.id}")
        started = time.monotonic()

        def show(stage: str, _start=started) -> None:
            print(f"          {time.monotonic() - _start:5.0f}s  {stage}", flush=True)

        try:
            run = prove(goal.goal, depth=args.depth, progress=show)
        except Exception as exc:
            consecutive_errors += 1
            results.append(
                ProofResult(
                    goal_id=goal.id,
                    area=goal.area,
                    tier=goal.tier,
                    outcome=ProofOutcome.ERROR,
                    detail=str(exc),
                )
            )
            print(f"ERROR  {str(exc)[:50]}")
            if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                print(
                    f"\nAborting: {CONSECUTIVE_ERROR_LIMIT} consecutive errors. "
                    "Nothing is reaching the model."
                )
                break
            continue

        consecutive_errors = 0
        result = result_from(goal, run)
        results.append(result)

        mark = {
            ProofOutcome.PROVED: "PROVED",
            ProofOutcome.NOT_PROVED: "not proved",
            ProofOutcome.NOT_FORMALIZED: "NOT FORMALISED",
        }[result.outcome]
        extra = f"  ({result.lemmas_proved}/{result.lemmas_total} lemmas)" if result.lemmas_total else ""
        print(f"          ----- {mark}{extra}  [{time.monotonic() - started:.0f}s]\n")

    summary = summarize(results)
    print()
    print(render(summary))

    OUT.write_text(
        json.dumps(
            {"summary": summary, "results": [r.__dict__ | {"tier": r.tier.value, "outcome": r.outcome.value} for r in results]},
            indent=2,
            default=str,
        )
    )
    print(f"\nSaved to {OUT}")

    # Unlike the verification gate, failing to prove is not a regression —
    # it is the expected state of the art. Only errors are a problem.
    return 1 if summary["errors"] == summary["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
