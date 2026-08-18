"""Fresh subprocess vs persistent REPL, on the same snippets, against real Lean.

    python scripts/compare_lean_modes.py

Needs Lean and Mathlib. This is a script and not a test for that reason —
`tests/test_mathv2_repl.py` covers the same contract offline against a fake
that speaks the real protocol.

WHAT IT ANSWERS, in order of importance:

  1. ISOLATION. Attempt 1 declares `leaked`; attempt 2 tries to use it. Under
     the REPL, attempt 2 MUST fail. If it succeeds, one proof attempt can
     satisfy the next and no proof rate produced this way means anything.
  2. EQUIVALENCE. Every snippet must be classified identically in both modes.
     A rate produced with MRA_LEAN_REPL=1 has to mean what a rate produced
     without it means, or the flag is not an optimisation but a different
     experiment.
  3. SPEED. What the whole exercise is for.

A DISAGREEMENT ON ANY SNIPPET FAILS THE RUN. That is deliberate: the REPL is
worth having only if it is indistinguishable from the thing it replaces.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MATHLIB = "import Mathlib\n"

# Chosen to cover every branch of `_classify` plus the two failure shapes the
# agent actually meets. Each is (label, source, why it is here).
SNIPPETS = [
    ("trivial proof",
     "theorem cmp_a : True := trivial"),
    ("arithmetic",
     "theorem cmp_b : 2 + 2 = 4 := by norm_num"),
    ("a real Mathlib lemma",
     "theorem cmp_c (n : Nat) : ∃ p, n ≤ p ∧ p.Prime := Nat.exists_infinite_primes n"),
    ("unknown identifier",
     "theorem cmp_d : True := by exact nonesuch_lemma"),
    ("unsolved goals",
     "theorem cmp_e : ∀ n : Nat, n = n + 1 := by intro n"),
    ("sorry is not a proof",
     "theorem cmp_f : True := by sorry"),
    ("an axiom is cheating",
     "axiom cmp_cheat : False\ntheorem cmp_g : True := trivial"),
    ("a suggestion tactic is cheating",
     "theorem cmp_h : True := by exact?"),
    ("an open header, as ProofNet statements carry",
     "open Complex Filter in\ntheorem cmp_i : True := trivial"),
]

ISOLATION = [
    ("declares a lemma", "theorem cmp_leaked : True := trivial"),
    ("tries to use it", "theorem cmp_uses : True := cmp_leaked"),
]


def compile_all(snippets, workdir):
    """Run each snippet in its own goal-scoped memo, in the current mode."""
    from math_v2.tools import _util

    results = []
    for label, body in snippets:
        _util.forget()
        scratch = os.path.join(workdir, label.replace(" ", "_"))
        os.makedirs(scratch, exist_ok=True)
        started = time.time()
        result = asyncio.run(_util.lean_runner(scratch)(MATHLIB + body))
        results.append((label, result.outcome, time.time() - started))
    return results


def isolation_check(workdir):
    """Both snippets through ONE runner, in order, sharing whatever the mode
    shares. This is the check that decides whether the REPL is usable."""
    from math_v2.tools import _util

    _util.forget()
    runner = _util.lean_runner(workdir)
    outcomes = []
    for _, body in ISOLATION:
        outcomes.append(asyncio.run(runner(MATHLIB + body)).outcome)
    return outcomes


def in_mode(repl: bool, fn, *args):
    """Run `fn` with the flag set, reloading the modules that read it."""
    import importlib

    os.environ["MRA_LEAN_BACKEND"] = "repl" if repl else "subprocess"
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
    from math_v2 import _local

    ok, why = _local.lean_available()
    if not ok:
        print(f"Lean is not available: {why}")
        print("Set MRA_LEAN_PROJECT to a Lake project with Mathlib.")
        return 2

    workdir = tempfile.mkdtemp(prefix="cmp_lean_")
    print(f"project: {_local.LEAN_PROJECT}")
    print(f"repl:    {os.getenv('MRA_LEAN_REPL_BIN') or 'lake exe repl'}\n")

    # ---------------------------------------------------------- 1. isolation
    print("=" * 68)
    print("ISOLATION — attempt 2 must NOT see what attempt 1 declared")
    print("=" * 68)
    failures = []
    for mode, repl in (("subprocess", False), ("REPL", True)):
        try:
            first, second = in_mode(repl, isolation_check,
                                    os.path.join(workdir, f"iso_{mode}"))
        except Exception as exc:  # noqa: BLE001
            print(f"  {mode:<12} could not run: {exc}")
            failures.append(f"isolation/{mode}")
            continue
        leaked = second.value == "compiled"
        print(f"  {mode:<12} declare -> {first.value:<12} "
              f"use it -> {second.value:<12} "
              + ("*** LEAKED ***" if leaked else "isolated"))
        if leaked:
            failures.append(f"ISOLATION BROKEN in {mode} mode")

    # -------------------------------------------- 2. equivalence and 3. speed
    print()
    print("=" * 68)
    print("EQUIVALENCE AND SPEED")
    print("=" * 68)
    try:
        fresh = in_mode(False, compile_all, SNIPPETS, os.path.join(workdir, "sub"))
        quick = in_mode(True, compile_all, SNIPPETS, os.path.join(workdir, "rpl"))
    except Exception as exc:  # noqa: BLE001
        print(f"could not complete: {exc}")
        return 1

    print(f"  {'snippet':<38} {'subprocess':<14} {'REPL':<14} {'':<8}")
    print("  " + "-" * 66)
    for (label, sub_outcome, sub_s), (_, repl_outcome, repl_s) in zip(fresh, quick):
        agree = sub_outcome is repl_outcome
        if not agree:
            failures.append(f"{label}: {sub_outcome.value} vs {repl_outcome.value}")
        print(f"  {label:<38} {sub_outcome.value:<9}{sub_s:5.1f}s "
              f"{repl_outcome.value:<9}{repl_s:5.1f}s "
              + ("" if agree else "  *** DISAGREE ***"))

    sub_total = sum(seconds for _, _, seconds in fresh)
    repl_total = sum(seconds for _, _, seconds in quick)
    print("  " + "-" * 66)
    print(f"  {'TOTAL':<38} {sub_total:13.1f}s {repl_total:13.1f}s")
    if repl_total > 0:
        print(f"\n  {sub_total / repl_total:.1f}x faster, "
              f"{sub_total / len(fresh):.1f}s -> {repl_total / len(quick):.1f}s "
              "per compile")
        print("  (the REPL's first compile includes the one-time Mathlib import)")

    print()
    if failures:
        print("FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        print("\nThe REPL path is NOT safe to benchmark with until these agree.")
        return 1

    print("Both modes agree on every snippet, and attempts are isolated in both.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
