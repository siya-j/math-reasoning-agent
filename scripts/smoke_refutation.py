"""The refutation path against a REAL compiler. No model, no API, no cost.

    python scripts/smoke_refutation.py
    set MRA_LEAN_REPL=1 && python scripts/smoke_refutation.py   # the REPL arm

WHY THIS EXISTS
---------------
Every unit test of `try_refutation` injects a fake `run_lean` that returns a
chosen `LeanOutcome`. That proves the POLICY is right — a CHEATED result is not
a refutation, a `sorry` never reaches the compiler — and proves nothing about
whether the assembled file compiles at all.

The gap is exactly where a new Lean path usually breaks: whether `¬ (∀ ...)`
survives `rename_goal`, whether the preamble imports enough to build a
counterexample, whether the REPL's session handles the extra declaration. None
of that is visible to a fake, and finding it during a benchmark sweep wastes
the sweep. So this compiles six real files and checks what came back.

WHAT A PASS MEANS
-----------------
That the machinery works, not that the agent will use it well. The first
ProofNet run is still the first time a model sees the tool.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from math_v2.core import log, proving, verdict           # noqa: E402
from math_v2.tools import _util                          # noqa: E402

# `∀ n : ℕ, n = 0` is false, and the refutation is small enough that a failure
# here is a failure of the PATH, never of the mathematics.
GOAL = "theorem mra_goal : ∀ n : ℕ, n = 0"
NEGATION = "theorem mra_refutation : ¬ (∀ n : ℕ, n = 0)"
HONEST = "by\n  intro h\n  have one_eq : (1 : ℕ) = 0 := h 1\n  omega"

# Each case: label, statement, proof, and whether it must end REFUTED.
CASES = [
    ("an honest refutation compiles", NEGATION, HONEST, True),
    ("`sorry` is refused before the compiler", NEGATION, "by\n  sorry", False),
    ("`admit` is refused before the compiler", NEGATION, "by\n  admit", False),
    # `exact?` COMPILES and reports a candidate. `interpret` must call it
    # CHEATED, which is the one anti-cheat route a fake cannot rehearse.
    ("a suggestion tactic is not a refutation", NEGATION, "by\n  exact?", False),
    # True, provable, and refutes nothing. The negation lint is what stops it.
    ("a true non-negation refutes nothing",
     "theorem mra_other : 2 + 2 = 4", "by norm_num", False),
    # A negation that is itself false, so it cannot compile.
    ("an unprovable negation is not a refutation",
     "theorem mra_bad : ¬ (∀ n : ℕ, n + 0 = n)", HONEST, False),
]


async def main() -> int:
    workroot = tempfile.mkdtemp(prefix="smoke_refute_")
    print(f"exec:   {_util.mode()}   lean:   {_util.lean_backend()}")
    print(f"workdir: {workroot}\n")

    failures = []
    for index, (label, statement, proof, want_refuted) in enumerate(CASES):
        workdir = os.path.join(workroot, f"case_{index}")
        log.clear(workdir)

        result = await proving.try_refutation(
            workdir, statement, proof, _util.lean_runner(workdir)
        )
        got = bool(verdict.verified_refutation(workdir))
        ok = got == want_refuted

        print(f"[{'ok ' if ok else 'FAIL'}] {label}")
        print(f"        refuted={got} expected={want_refuted}"
              f" error={result.get('error', '-')}")
        if not ok:
            failures.append(label)
            print(f"        {result.get('message', '')[:400]}\n")

    # The end-to-end claim: a verified refutation is what upgrades the report,
    # and `finish` derives that from the record rather than from the summary.
    print()
    workdir = os.path.join(workroot, "finish")
    log.clear(workdir)
    log.append(workdir, log.Record(kind=log.PROOF, statement=GOAL, proof="by simp",
                                   status=log.FALSE, detail="unsolved goals"))
    await proving.try_refutation(workdir, NEGATION, HONEST,
                                 _util.lean_runner(workdir))
    upgraded = bool(verdict.verified_refutation(workdir))
    print(f"[{'ok ' if upgraded else 'FAIL'}] finish would report "
          f"{verdict.REFUTED if upgraded else verdict.STATEMENT_SUSPECT}")
    if not upgraded:
        failures.append("finish upgrade")

    print("\n" + "=" * 52)
    if failures:
        print(f"  {len(failures)} FAILED: {', '.join(failures)}")
        print("  Do not run ProofNet until these are understood.")
        return 1
    print("  all clear — the refutation path works against real Lean")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
