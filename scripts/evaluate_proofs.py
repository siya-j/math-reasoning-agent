"""Run the prover over the goal set and report four separate numbers.

    python scripts/evaluate_proofs.py --tier in-mathlib
    python scripts/evaluate_proofs.py --limit 3 --depth 0

Needs a model and Lean. Budget carefully: at LEMMA_DEPTH=1 a single goal
costs roughly 28 model calls and five Lean invocations.
"""

from __future__ import annotations

import argparse
import datetime
import json
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from eval.proof_dataset import Tier, load_goals  # noqa: E402
from eval.proof_metrics import (  # noqa: E402
    EXTERNAL,
    ProofOutcome,
    ProofResult,
    rehydrate,
    render,
    result_from,
    summarize,
)
from llm.reviewer import Reviewer  # noqa: E402
from pipeline.proving import environment, prove  # noqa: E402
from verifiers.lean_runner import lean_is_available  # noqa: E402

# The same default seed `scripts/sample_by_area.py` uses, so a shuffled
# evaluation and a shuffled sample agree on what "seed 20260101" means.
DEFAULT_SEED = 20260101
DEFAULT_OUT = Path(__file__).parent.parent / "eval" / "last_proof_run.json"
CONSECUTIVE_ERROR_LIMIT = 3

# Fixed once per process, so every incremental save carries the run's own
# start time rather than the time of its most recent write.
_STARTED = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

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
            # EVERY FIELD, via `rehydrate`, which reads the field list off the
            # dataclass. This loop used to name ten fields by hand while
            # `ProofResult` had twenty-four, and since `save` writes the whole
            # `__dict__` back, a resume overwrote the file with the fourteen
            # it had not heard of set to their defaults. That destroyed the
            # accepted proofs, attempts, traces and telemetry of thirty-two
            # decided goals in eval/results/mixed-1.json, and printed a
            # perfectly ordinary-looking summary while doing it.
            carried.append(rehydrate(row))
        except (KeyError, ValueError, TypeError):
            continue
    return carried


def invocation(args, profile: dict) -> dict:
    """WHAT PRODUCED THESE NUMBERS. Recorded because it was not.

    `environment()` records the Lean backend, and its own docstring gives the
    reason: "A benchmark number that cannot be attributed to a backend is not
    a measurement." That is exactly right and it was only half applied -- the
    MODEL, the BUDGET, the GOALS FILE and the CODE VERSION were printed to the
    terminal at startup and then lost. A results file said 40% and could not
    say 40% of what, by which model, under which budget, at which commit.

    For a technical audience that is not a caveat, it is the first question,
    and the answer was in someone's scrollback. Cheap to record and impossible
    to reconstruct later, which is the definition of something that belongs in
    the artefact.
    """
    commit = ""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=30, cwd=str(Path(__file__).resolve().parent.parent),
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - a missing git must not fail a run
        commit = ""

    return {
        "model": config.MODEL,
        "prover": config.PROVER,
        "goals_file": str(getattr(args, "goals", "") or ""),
        "budget_profile": getattr(args, "budget_profile", None) or "",
        # The profile's actual values, not just its name: the name means
        # nothing to a reader six months later, and the values are what the
        # run was bounded by.
        "budget": dict(profile),
        "limit": getattr(args, "limit", None),
        # Whether the order was shuffled, and with which seed. Without this a
        # partial run's rate cannot be read: front-loaded easy goals and a
        # cross-tier sample produce very different numbers from the same file.
        "shuffle": bool(getattr(args, "shuffle", False)),
        "seed": getattr(args, "seed", None) if getattr(args, "shuffle", False) else None,
        "tier": getattr(args, "tier", None) or "",
        "single_goal": getattr(args, "goal", None) or "",
        "commit": commit,
        "started": _STARTED,
    }


def save(results, summary, out: Path, run_info: dict | None = None) -> None:
    """Write after every goal, so an interrupted run loses nothing."""
    out.write_text(
        json.dumps(
            {
                "run": run_info or {},
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


def apply_budget_profile(name: str) -> dict:
    """The named profile's environment defaults, applied and returned.

    Routed through `pipeline.proving.budget_profile` — the same seam
    `environment()`/`prove()` use, so this evaluator never imports a prover
    directly. Returns what was applied, for the run's own printed summary.
    """
    from pipeline.proving import budget_profile

    return budget_profile(name)


def provenance_note(goals) -> str:
    """What to say, BEFORE the first model call, about what this run can show.

    `render` says this too, but by then the run is paid for -- and
    `eval/proofs.json`, the file used when no `--goals` is given, is 100%
    goals we wrote ourselves. So a plain `python scripts/evaluate_proofs.py`
    is exactly the case that needs catching, and catching it at the end is
    catching it too late.

    MEASURED: our own tiers are 141/166 across every results file on disk,
    and 11 of those 25 goals have a perfect record over four or more runs
    (scripts/retire_saturated.py). A goal that has never failed cannot show
    an improvement.
    """
    if any(g.tier in EXTERNAL for g in goals):
        return ""
    return (
        "NOTE: every goal here is one we wrote ourselves. Our own tiers are\n"
        "      141/166 across every run on disk, and 11 of the 25 have never\n"
        "      once failed -- this is a regression canary, not a capability\n"
        "      measurement. For a number worth quoting:\n"
        "        --goals eval/proofnet-sharp.json   (371 goals, corrected)\n"
        "        --goals eval/proofs-live.json      (the 14 of ours that "
        "still move)\n"
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
        "--shuffle", action="store_true",
        help="shuffle the goals before --limit, so an interrupted run or a "
             "limited one samples across tiers instead of taking the easy "
             "ones first. eval/mixed-benchmark.json is ordered easy to hard, "
             "so a run stopped partway reports an upper bound, not an "
             "estimate.",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help=f"seed for --shuffle, so the order is reproducible and --resume "
             f"sees the same sequence (default {DEFAULT_SEED})",
    )
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
    parser.add_argument(
        "--budget-profile", choices=("hard-reasoning",),
        help="apply a named set of larger budget defaults (see "
        "pipeline.proving.budget_profile) before the first prove() call. "
        "An env var you already exported always wins -- this only fills in "
        "what is not already set. No-op under a prover other than the one "
        "the profile's env vars belong to.",
    )
    parser.add_argument(
        "--pace", type=float, default=0.0,
        help="seconds to sleep BETWEEN goals (default 0, off). MEASURED: a "
        "long run can hit a rate limit partway through, and the model "
        "client's own retry-with-backoff for that is invisible to the wall "
        "clock -- it can burn the whole per-goal budget just backing off, "
        "turning a goal that would otherwise have settled into a bare "
        "'budget exhausted'. A gap between goals spends wall time "
        "proactively, before hitting the ceiling, rather than reactively "
        "after.",
    )
    args = parser.parse_args()
    out = Path(args.out) if args.out else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)

    # Loaded ONCE, and `--goal` is validated against THIS dataset. It used to
    # call `load_goals()` a second time with no argument, so a `--goal` from a
    # `--goals` file was checked against the default `eval/proofs.json` and
    # every ProofNet id was rejected as "No such goal".
    #
    # Validated against `dataset` rather than the filtered `goals`, so
    # `--tier`/`--area` combined with `--goal` still report an id that exists
    # but was filtered out as a selection of nothing rather than a typo.
    dataset = load_goals(Path(args.goals) if args.goals else None)
    goals = list(dataset)
    if args.tier:
        goals = [g for g in goals if g.tier.value == args.tier]
    if args.area:
        goals = [g for g in goals if g.area == args.area]
    if args.goal:
        wanted = set(args.goal)
        unknown = wanted - {g.id for g in dataset}
        if unknown:
            print(f"No such goal: {', '.join(sorted(unknown))}")
            return 2
        goals = [g for g in goals if g.id in wanted]

    # SHUFFLED BEFORE `--limit`, deliberately, which is the whole point.
    #
    # MEASURED on eval/mixed-benchmark.json, whose goals are ordered easy to
    # hard: in-mathlib and near-mathlib at positions 1-13, hard and deep at
    # 16-25, proofnet at 26-45, putnam at 46-65. A run interrupted at position
    # 33 -- which is exactly what happened -- has therefore completed every
    # self-authored tier and only seven ProofNet goals, and its 75% proof rate
    # is an UPPER BOUND rather than an estimate. The 33 goals left were the
    # two hardest blocks.
    #
    # The same ordering makes cost look like it is degrading mid-run: model
    # calls per goal climb from ~7.6 on the in-mathlib block to ~18 on
    # ProofNet and ~24 on Putnam, purely because the goals get harder.
    #
    # Shuffling means an interrupted run, or a `--limit`, samples ACROSS tiers
    # instead of front-loading the easy ones. Seeded, so the order is
    # reproducible and `--resume` sees the same sequence; the seed is recorded
    # in the results file with everything else.
    if args.shuffle:
        random.Random(args.seed).shuffle(goals)

    if args.limit:
        goals = goals[: args.limit]

    if not goals:
        print("No goals matched.")
        return 2

    note = provenance_note(goals)
    if note:
        print(note)

    profile: dict = {}
    if args.budget_profile:
        profile = apply_budget_profile(args.budget_profile)
        print(f"budget profile: {args.budget_profile} "
              f"({', '.join(f'{k}={v}' for k, v in profile.items())})")
    elif any(g.tier.value in ("putnam", "hard", "deep") for g in goals):
        print("hint: this selection includes putnam/hard/deep goals; "
              "consider --budget-profile hard-reasoning\n")

    # Built AFTER the profile is applied, so the budget it records is the one
    # the run actually used rather than the defaults it started from.
    run_info = invocation(args, profile)

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
        # BEFORE the goal, not after -- applies regardless of whether the
        # previous goal errored, and `index` is already 1-based over the
        # post-`--resume`-filtering list, so this is exactly "not the first
        # goal THIS invocation runs" without tracking anything separately.
        if args.pace and index > 1:
            time.sleep(args.pace)
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

        result = result_from(goal, run)
        results.append(result)
        save(results, summarize(results), out, run_info)  # survive an abort later

        mark = mark_for(result.outcome)
        extra = f"  ({result.lemmas_proved}/{result.lemmas_total} lemmas)" if result.lemmas_total else ""
        print(f"          ----- {mark}{extra}  [{run.telemetry.summary()}]\n")

        # A RETURNED ERROR COUNTS TOO, and until now it did not.
        #
        # This guard only saw exceptions that `prove()` RAISED. A prover may
        # catch its own -- "a crash must not lose the record" is a reasonable
        # thing for one to do -- and return a ProofRun whose trace says the
        # agent failed. `prove()` then returns normally, and the counter above
        # was reset on every goal.
        #
        # Deliberately phrased without naming a prover: this file drives
        # whichever one is configured, and
        # `test_evaluate_proofs_never_imports_a_prover_directly` fails if that
        # slips -- as it did on the first draft of this comment.
        #
        # MEASURED on eval/results/proofnet-60.json: an INVALID (not missing)
        # API key let the model BUILD and then failed at call time, inside the
        # harness's catch. All 53 remaining goals ran and errored identically
        # in zero seconds. The first time this happened the key was ABSENT,
        # `get_model()` raised at build time, and the abort worked as intended
        # -- which is why the hole went unnoticed.
        #
        # Nothing was billed that time because no call succeeded. A quota or
        # auth failure part-way through a paid run is the case that would
        # cost: the counter is what stops it.
        if result.outcome is ProofOutcome.ERROR:
            consecutive_errors += 1
            if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                print(f"\nAborting: {CONSECUTIVE_ERROR_LIMIT} consecutive "
                      "errors. Nothing is reaching the model.")
                break
        else:
            consecutive_errors = 0

    summary = summarize(results)
    print()
    print(render(summary))

    save(results, summary, out, run_info)
    print(f"\nSaved to {out}")

    # Unlike the verification gate, failing to prove is not a regression —
    # it is the expected state of the art. Only errors are a problem.
    return 1 if summary["errors"] == summary["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
