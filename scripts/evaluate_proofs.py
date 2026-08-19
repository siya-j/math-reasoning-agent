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
from pipeline.proving import environment, prove  # noqa: E402
from verifiers.lean_runner import lean_is_available  # noqa: E402

DEFAULT_OUT = Path(__file__).parent.parent / "eval" / "last_proof_run.json"
CONSECUTIVE_ERROR_LIMIT = 3

# How each outcome prints on the per-goal line.
#
# EXHAUSTIVE OVER ProofOutcome, and there is a test that says so. This was a
# dict literal indexed inline, covering three of the six members; adding
# SUSPECT_STATEMENT and EXHAUSTED to the enum crashed a live benchmark run with
# a KeyError on the FIRST goal, after the model call had already been paid for.
# A reporting layer that can abort a run is not a reporting layer.
#
# Case carries meaning: a proof is the only result shouted. NOT FORMALISED and
# SUSPECT STATEMENT are capitalised because they are statements about the
# benchmark row rather than about the prover, and reading them as prover
# failures is the exact mistake this vocabulary exists to prevent.
MARKS = {
    ProofOutcome.PROVED: "PROVED",
    ProofOutcome.NOT_PROVED: "not proved",
    ProofOutcome.NOT_FORMALIZED: "NOT FORMALISED",
    ProofOutcome.REFUTED: "REFUTED (negation compiled)",
    ProofOutcome.SUSPECT_STATEMENT: "SUSPECT STATEMENT (unverified)",
    ProofOutcome.EXHAUSTED: "budget exhausted",
    ProofOutcome.ERROR: "ERROR",
}


def mark_for(outcome: ProofOutcome) -> str:
    """Never raises. A missing label must not end a run that is paying for
    model calls — it degrades to the outcome's own name and the run continues.
    The test keeps MARKS complete; this keeps the run alive if it ever is not.
    """
    return MARKS.get(outcome, outcome.value.replace("_", " "))


def completed(resume: bool, out: Path):
    """Goals already DECIDED in the previous run.

    Errors are deliberately excluded: a goal that never reached the model was
    not decided, and skipping it would bake a quota outage into the results.
    """
    if not resume or not out.exists():
        return []
    try:
        saved = json.loads(out.read_text())
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


def save(results, summary, out: Path) -> None:
    """Write after every goal, so an interrupted run loses nothing."""
    out.write_text(
        json.dumps(
            {
                "environment": environment(),
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
    parser.add_argument(
        "--goals",
        help="a goals file other than the default eval/proofs.json — e.g. "
        "eval/proofnet.json. The seven-goal near-mathlib set is never "
        "touched by this.",
    )
    parser.add_argument("--tier", choices=[t.value for t in Tier])
    parser.add_argument("--area")
    parser.add_argument(
        "--goal",
        action="append",
        help="run only this goal id; repeatable. Selecting one goal with "
        "--limit depends on dataset ORDER, which is not a stable way to "
        "name a thing.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--out",
        help="where to write the results. Each run OVERWRITES the default, so comparing two configurations needs a separate file for each.",
    )
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
    out = Path(args.out) if args.out else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)

    goals = load_goals(Path(args.goals) if args.goals else None)
    if args.tier:
        goals = [g for g in goals if g.tier.value == args.tier]
    if args.area:
        goals = [g for g in goals if g.area == args.area]
    if args.goal:
        wanted = set(args.goal)
        unknown = wanted - {g.id for g in load_goals()}
        if unknown:
            print(f"No such goal: {', '.join(sorted(unknown))}")
            return 2
        goals = [g for g in goals if g.id in wanted]
    if args.limit:
        goals = goals[: args.limit]

    if not goals:
        print("No goals matched.")
        return 2

    if not lean_is_available():
        print("WARNING: Lean not found. Every goal will be NOT PROVED.\n")

    reviewer = Reviewer() if args.review else None

    print(f"model: {config.MODEL}")
    print(f"prover: {config.PROVER}")
    where = environment()
    if where:
        print(f"exec:   {where.get('execution_mode', '?')}   "
              f"lean:   {where.get('lean_backend', '?')}"
              + (f"   toolchain: {where['lean_toolchain']}"
                 if where.get("lean_toolchain") else ""))
    print(f"review: {'on' if reviewer else 'off'}")
    print(f"goals: {len(goals)}  depth: {args.depth if args.depth is not None else config.LEMMA_DEPTH}\n")

    # A goal costs minutes and dozens of calls. Losing completed work to a
    # rate limit on a later goal is pure waste, so decided outcomes carry over
    # and only errors are retried.
    results: list[ProofResult] = list(completed(args.resume, out))
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
        save(results, summarize(results), out)  # survive an abort on a later goal

        mark = mark_for(result.outcome)
        extra = f"  ({result.lemmas_proved}/{result.lemmas_total} lemmas)" if result.lemmas_total else ""
        print(f"          ----- {mark}{extra}  [{run.telemetry.summary()}]\n")

    summary = summarize(results)
    print()
    print(render(summary))

    save(results, summary, out)
    print(f"\nSaved to {out}")

    # Unlike the verification gate, failing to prove is not a regression —
    # it is the expected state of the art. Only errors are a problem.
    return 1 if summary["errors"] == summary["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
