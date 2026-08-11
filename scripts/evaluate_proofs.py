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
from llm.reviewer import Reviewer  # noqa: E402
from pipeline.prover import prove  # noqa: E402
from verifiers.lean_runner import lean_is_available  # noqa: E402

OUT = Path(__file__).parent.parent / "eval" / "last_proof_run.json"
CONSECUTIVE_ERROR_LIMIT = 3


def completed(resume: bool):
    """Goals already DECIDED in the previous run.

    Errors are deliberately excluded: a goal that never reached the model was
    not decided, and skipping it would bake a quota outage into the results.
    """
    if not resume or not OUT.exists():
        return []
    try:
        saved = json.loads(OUT.read_text())
    except (ValueError, OSError):
        return []

    carried = []
    for row in saved.get("results", []):
        if row.get("outcome") == ProofOutcome.ERROR.value:
            continue
        try:
            carried.append(
                ProofResult(
                    goal_id=row["goal_id"],
                    area=row["area"],
                    tier=Tier(row["tier"]),
                    outcome=ProofOutcome(row["outcome"]),
                    statement=row.get("statement", ""),
                    attempts=row.get("attempts", 0),
                    lemmas_total=row.get("lemmas_total", 0),
                    lemmas_proved=row.get("lemmas_proved", 0),
                    via_synthesis=row.get("via_synthesis", False),
                    detail=row.get("detail", ""),
                )
            )
        except (KeyError, ValueError):
            continue
    return carried


def save(results, summary) -> None:
    """Write after every goal, so an interrupted run loses nothing."""
    OUT.write_text(
        json.dumps(
            {
                "summary": summary,
                "results": [
                    r.__dict__ | {"tier": r.tier.value, "outcome": r.outcome.value}
                    for r in results
                ],
            },
            indent=2,
            default=str,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=[t.value for t in Tier])
    parser.add_argument("--area")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--depth", type=int, default=None, help="override config.LEMMA_DEPTH"
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="check each accepted statement against the question (one extra call)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip goals already decided in the last run (not errors)",
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

    reviewer = Reviewer() if args.review else None

    print(f"model: {config.MODEL}")
    print(f"review: {'on' if reviewer else 'off'}")
    print(f"goals: {len(goals)}  depth: {args.depth if args.depth is not None else config.LEMMA_DEPTH}\n")

    # A goal costs minutes and dozens of calls. Losing completed work to a
    # rate limit on a later goal is pure waste, so decided outcomes carry over
    # and only errors are retried.
    results: list[ProofResult] = list(completed(args.resume))
    done = {r.goal_id for r in results}
    if done:
        print(f"resuming: {len(done)} goal(s) already decided\n")
        goals = [g for g in goals if g.id not in done]

    consecutive_errors = 0

    for index, goal in enumerate(goals, start=1):
        print(f"[{index}/{len(goals)}] {goal.id}")
        started = time.monotonic()

        def show(stage: str, _start=started) -> None:
            print(f"          {time.monotonic() - _start:5.0f}s  {stage}", flush=True)

        try:
            run = prove(
                goal.goal, depth=args.depth, progress=show, reviewer=reviewer
            )
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
            # Show enough of the error to act on. Truncating to 50 characters
            # turned a diagnosable API fault into "INVALID_AR...".
            print(f"          ERROR  {str(exc)[:400]}")
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
        save(results, summarize(results))   # survive an abort on a later goal

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

    save(results, summary)
    print(f"\nSaved to {OUT}")

    # Unlike the verification gate, failing to prove is not a regression —
    # it is the expected state of the art. Only errors are a problem.
    return 1 if summary["errors"] == summary["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
