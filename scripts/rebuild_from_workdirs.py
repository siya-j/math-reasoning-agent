"""Rebuild a results file from the per-goal workspaces the run left behind.

WHY THIS EXISTS. `eval/results/mixed-1.json` was overwritten by `--resume`,
which rebuilt each carried-forward goal from ten named fields while
`ProofResult` had twenty-four (fixed in `eval.proof_metrics.rehydrate`; this
script is the cleanup, not the fix). Everything below the outcomes was lost:
the accepted proofs, the attempts, the traces, the telemetry.

`math_v2.harness.prove` gives each goal a `tempfile.mkdtemp(prefix="mathv2_")`
workspace and mkdtemp never cleans up, so the on-disk record survived in the
system temp directory and is preserved in `eval/evidence/`.

HOW IT REBUILDS, and why not by hand. It calls `harness._to_proof_run` on the
recovered workspace -- the SAME function the live run used -- and then
`eval.proof_metrics.result_from`. So the rebuilt row is derived exactly as the
original was, including re-deriving the verdict from the records rather than
trusting anything written down about it. A bespoke reimplementation here would
be a second, unverified derivation of the thing the whole system is about.

WHAT IT CANNOT RECOVER, and refuses to fake. `model_calls`, `input_tokens`,
`output_tokens` and `seconds` are read off the agent's returned transcript and
the harness's clock. They were never on disk. They are passed as zero with
`telemetry_complete=False`, so every rebuilt row reports `cost_complete:
false` and the summary says the cost is unknown. Leaving `cost_complete` at
its default True would have the rebuilt file certify wiped numbers as
measured -- the same failure that caused this, in a new coat.

THE SELF-CHECK. The outcomes SURVIVED the overwrite, so they are an
independent control: a rebuilt outcome that disagrees with the file's is
reported and, unless --force, aborts the rebuild. The rebuild derives the
outcome from the records without consulting the old row, so agreement on all
thirty-two is evidence the workspaces really are the ones that produced them.

USAGE
    python scripts/rebuild_from_workdirs.py \
        --evidence eval/evidence/mixed-1/workdirs \
        --results  eval/results/mixed-1.json \
        --out      eval/results/mixed-1-rebuilt.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _apply_budget(results: dict) -> None:
    """The run's own budget, exported BEFORE math_v2 is imported.

    `budget.MAX_LEAN_CALLS` is a module-level constant read from the
    environment at first import, and `_to_proof_run` records it as
    `lean_budget`. Import math_v2 first and every rebuilt row claims the
    default ceiling of 12 for a run that was given 40.
    """
    for name, value in (results.get("run", {}).get("budget") or {}).items():
        os.environ[name] = str(value)


def _keys(log: dict) -> set:
    """Every formal statement this workspace is about.

    The log's `goal` alone is not enough: on a goal where the agent wandered
    off, `goal` holds the diversion it last worked on (`exists_basis` on
    `lin-vector-space-basis`) rather than the goal it was given. The
    statements inside the records still name the real one.
    """
    found = {(log.get("goal") or "").strip()}
    for record in log.get("records") or []:
        found.add((record.get("statement") or "").strip())
    return found - {""}


def _index(evidence: Path) -> list:
    out = []
    for workdir in sorted(evidence.glob("mathv2_*")):
        path = workdir / "math" / "proof_log.json"
        if not path.exists():
            continue
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append((workdir, _keys(log)))
    return out


def rebuild(evidence: Path, results_path: Path, out: Path, force: bool) -> int:
    saved = json.loads(results_path.read_text(encoding="utf-8"))
    _apply_budget(saved)

    from domain.proof import ProofRun
    from eval.proof_dataset import Tier
    from eval.proof_metrics import ProofResult, rehydrate, result_from, summarize
    from math_v2 import harness

    class _Goal:
        """`result_from` reads three attributes off a Goal. The dataset is not
        loaded because a rebuild must not depend on the goals file still
        saying what it said when the run happened."""

        def __init__(self, row):
            self.id = row["goal_id"]
            self.area = row["area"]
            self.tier = Tier(row["tier"])

    index = _index(evidence)
    rebuilt, carried, disagreed = [], [], []

    for row in saved.get("results", []):
        statement = (row.get("statement") or "").strip()
        hits = [w for w, keys in index if statement and statement in keys]

        if len(hits) != 1:
            # No evidence, or evidence that cannot be pinned to this goal.
            # An ERROR row is the ordinary case: it never reached the model,
            # so it has no statement and no workspace.
            carried.append((row["goal_id"], row.get("outcome"),
                            "ambiguous" if hits else "no workspace"))
            rebuilt.append(rehydrate(row))
            continue

        workdir = str(hits[0])
        run = harness._to_proof_run(
            ProofRun(goal=row["goal_id"]), workdir, prose="", seconds=0.0,
            model_calls=0, tokens=(0, 0), telemetry_complete=False,
        )
        result = result_from(_Goal(row), run)

        if result.outcome.value != row.get("outcome"):
            disagreed.append((row["goal_id"], row.get("outcome"),
                              result.outcome.value))
        rebuilt.append(result)

    print(f"rows            {len(rebuilt)}")
    print(f"  rebuilt       {len(rebuilt) - len(carried)}  from a workspace")
    print(f"  carried as-is {len(carried)}")
    for goal_id, outcome, why in carried:
        print(f"      {goal_id}  ({outcome}) -- {why}")

    if disagreed:
        print("\nOUTCOME DISAGREEMENT -- the rebuild does not describe this run:")
        for goal_id, was, now in disagreed:
            print(f"      {goal_id}: file says {was}, records say {now}")
        if not force:
            print("\nRefusing to write. Pass --force only if you know why.")
            return 1
    else:
        print("\nself-check: every rebuilt outcome matches the one that "
              "survived the overwrite")

    recovered = sum(1 for r in rebuilt if r.proof)
    print(f"accepted proofs with their Lean source: {recovered}")

    payload = {
        "run": saved.get("run", {}),
        "environment": saved.get("environment", {}),
        "rebuilt": {
            "from": str(evidence),
            "of": str(results_path),
            "why": "the file was overwritten by --resume; see "
                   "eval/evidence/mixed-1/README.md",
            "unrecoverable": ["model_calls", "input_tokens", "output_tokens",
                              "seconds"],
        },
        "summary": summarize(rebuilt),
        "results": [r.__dict__ | {"tier": r.tier.value,
                                  "outcome": r.outcome.value}
                    for r in rebuilt],
    }
    out.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--force", action="store_true",
                        help="write even if a rebuilt outcome disagrees with "
                             "the one that survived")
    args = parser.parse_args()
    return rebuild(args.evidence, args.results, args.out, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
