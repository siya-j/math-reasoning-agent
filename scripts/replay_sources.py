"""Replay every Lean source a real run produced, through BOTH backends.

    python scripts/replay_sources.py eval/results/*.json

THE STRONGEST BACKEND-EQUIVALENCE EVIDENCE AVAILABLE, AND IT COSTS NOTHING.
The sources were already paid for. No model is called here — the statements and
proofs come out of results files the agent wrote, and each is recompiled twice.

WHY THIS EXISTS
---------------
The near-Mathlib A/B disagreed on two of seven goals, and per-goal equality was
the wrong acceptance test: the arms did not follow the same trajectory. The
subprocess arm's own trace says why —

    budget: 293s of the 300s budget used - under 60s left,
            too little to finish a compilation (slowest seen here: 52s)

— it ran out of WALL CLOCK, made 15 Lean calls where the REPL made 33, and one
goal never got the time to elaborate a statement at all. That is the compile
budget being the binding constraint, not the backends disagreeing.

But "the difference was trajectory" was an inference. This turns it into a
check, and a much stronger one than the nine snippets, because these are
sources the agent actually wrote rather than cases someone thought to include:

    IF every recorded source classifies identically in both backends,
    THEN the outcome differences cannot be backend semantics.

That is an implication, not a statistic. It needs no repeats.

WHAT IT CANNOT SEE
------------------
`ProofResult.stages` truncates proofs to 600 characters. A longer proof cannot
be replayed faithfully and is reported as SKIPPED with a count, never silently
dropped — a comparison that quietly ignores its hard cases is worthless.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TRUNCATED = 600


def sources_from(path: Path):
    """Every (label, statement, proof) a results file recorded.

    Both the statement checks and the proof attempts, because they exercise
    different parts of the pipeline: a statement check compiles a signature
    with `sorry` (so it must classify INCOMPLETE, which is the anti-cheat
    path), and an attempt compiles a real proof.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"  could not read {path.name}: {exc}")
        return []

    found = []
    for row in data.get("results", []):
        statement = (row.get("statement") or "").strip()
        if not statement:
            continue
        goal = row.get("goal_id", "?")

        # The signature with a placeholder — what `check_statement` compiles.
        found.append((f"{goal}/statement", statement, "sorry"))

        for index, stage in enumerate(row.get("stages") or [], start=1):
            proof = (stage.get("proof") or "").strip()
            if not proof:
                continue
            found.append((f"{goal}/attempt{index}", statement, proof))
    return found


def build(statement, proof):
    from verifiers.lean_verifier import build_source

    return build_source(statement, proof)


def compile_all(sources, workdir):
    """Every source through the CURRENT backend. Memo cleared between each, so
    a repeated source is genuinely recompiled rather than answered from cache —
    the point is to compare Lean, not our bookkeeping."""
    from math_v2.tools import _util

    results = []
    for index, (label, statement, proof) in enumerate(sources):
        _util.forget()
        scratch = os.path.join(workdir, f"s{index:03d}")
        os.makedirs(scratch, exist_ok=True)
        started = time.time()
        outcome = asyncio.run(_util.lean_runner(scratch)(build(statement, proof)))
        results.append((label, outcome.outcome, outcome.output, time.time() - started))
    return results


def in_backend(backend, fn, *args):
    os.environ["MRA_LEAN_BACKEND"] = backend
    os.environ.pop("MRA_LEAN_REPL", None)
    from math_v2.tools import _repl, _util

    importlib.reload(_repl)
    importlib.reload(_util)
    _repl.shutdown()
    try:
        return fn(*args)
    finally:
        _repl.shutdown()


def main() -> int:
    os.environ.setdefault("MRA_EXEC", "local")

    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        paths = sorted((ROOT / "eval" / "results").glob("*.json"))
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("No results files. Pass some, or run a benchmark first.")
        return 2

    from math_v2 import _local

    ok, why = _local.lean_available()
    if not ok:
        print(f"Lean is not available: {why}")
        return 2
    if not os.getenv("MRA_LEAN_REPL_BIN"):
        print("Set MRA_LEAN_REPL_BIN to the built repl binary.")
        return 2

    # Deduplicated: the same source compiled twice tells us nothing twice, and
    # each subprocess compile costs ~40s.
    seen = set()
    sources, skipped = [], []
    for path in paths:
        print(f"reading {path.name}")
        for label, statement, proof in sources_from(path):
            key = (statement, proof)
            if key in seen:
                continue
            seen.add(key)
            if len(proof) >= TRUNCATED:
                skipped.append(label)
                continue
            sources.append((label, statement, proof))

    if not sources:
        print("No replayable sources found.")
        return 2

    print(f"\n{len(sources)} unique sources to replay, "
          f"{len(skipped)} skipped as truncated at {TRUNCATED} chars")
    print(f"estimated: subprocess ~{len(sources) * 40 / 60:.0f} min, "
          f"REPL ~{len(sources) * 0.5 / 60 + 1:.0f} min\n")

    print("subprocess arm...")
    fresh = in_backend("subprocess", compile_all, sources,
                       str(ROOT / "eval" / "replay" / "sub"))
    print("REPL arm...")
    quick = in_backend("repl", compile_all, sources,
                       str(ROOT / "eval" / "replay" / "rpl"))

    print(f"\n{'=' * 76}")
    print("  EVERY SOURCE, BOTH BACKENDS")
    print(f"{'=' * 76}")
    print(f"  {'source':<34} {'subprocess':<14} {'repl':<14}")
    print("  " + "-" * 72)

    disagreements = []
    for (label, sub_outcome, sub_text, sub_s), (_, repl_outcome, repl_text, repl_s) in zip(fresh, quick):
        agree = sub_outcome is repl_outcome
        if not agree:
            disagreements.append((label, sub_outcome, sub_text, repl_outcome, repl_text))
        print(f"  {label:<34} {sub_outcome.value:<14} {repl_outcome.value:<14}"
              + ("" if agree else "  *** DISAGREE ***"))

    sub_total = sum(row[3] for row in fresh)
    repl_total = sum(row[3] for row in quick)
    print("  " + "-" * 72)
    print(f"  {'TOTAL':<34} {sub_total:10.0f}s {repl_total:13.0f}s")
    if repl_total > 0:
        print(f"  {'per compile':<34} {sub_total / len(fresh):10.1f}s "
              f"{repl_total / len(quick):13.1f}s     "
              f"{sub_total / repl_total:.1f}x")

    if skipped:
        print(f"\n  SKIPPED (proof truncated in the results file, "
              f"cannot replay faithfully): {len(skipped)}")
        for label in skipped[:10]:
            print(f"    {label}")

    print()
    if disagreements:
        print(f"{len(disagreements)} DISAGREEMENT(S) — the backends are not equivalent:\n")
        for label, sub_outcome, sub_text, repl_outcome, repl_text in disagreements:
            print(f"  {label}")
            print(f"    subprocess {sub_outcome.value}: {sub_text[:200]}")
            print(f"    repl       {repl_outcome.value}: {repl_text[:200]}\n")
        return 1

    print(f"All {len(fresh)} sources classified IDENTICALLY in both backends.")
    print()
    print("Therefore the near-Mathlib per-goal differences cannot be backend")
    print("semantics. They are trajectory — which the subprocess arm's own")
    print("trace already said: 293s of a 300s budget spent, 15 Lean calls")
    print("against the REPL's 33.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
