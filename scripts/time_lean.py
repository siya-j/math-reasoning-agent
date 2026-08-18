"""How long does one Lean compile actually cost on THIS machine?

    python scripts/time_lean.py

WHY THIS EXISTS
---------------
Two budget bugs in a row came from a constant guessed on different hardware.
The reserve assumed a compile costs 60s; on Windows a statement check took
roughly 340s, so exercise_1_19b spent 1032s against a 300s budget. The budget
now measures this itself at run time, but before choosing a budget at all it is
worth knowing the number — and, more importantly, WHY it is what it is.

WHY IT WARMS UP FIRST — A BUG THIS SCRIPT USED TO HAVE
------------------------------------------------------
The first version timed each case once, in order, and reported
`import Mathlib` minus `empty file` as "the cost of importing Mathlib". On a
real machine it printed:

    empty file                   8.1s
    import Mathlib              93.2s
    import + one theorem        43.1s     <- MORE work, LESS time

Case 3 does everything case 2 does and finishes in under half the time, which
is impossible unless the two were not measured under the same conditions. They
were not: case 2 cold-reads several GB of .olean files from disk and case 3
finds them in the OS page cache. Subtracting one from the other produced a
number (85.1s) that described neither.

So every case is now run twice and only the SECOND reading is reported. That is
the steady state, and the steady state is what the agent actually experiences —
after the first goal of a benchmark run, the cache is warm.

WHAT THE THREE TIMINGS MEAN

  1. empty file           process startup, Lake resolution. No Mathlib.
  2. `import Mathlib`     the whole library loaded from .olean files.
  3. a trivial theorem    the same import, plus elaborating one line.

Row 1 is the floor: no compile can be faster. Row 3 minus row 1 is the import
cost, and it is paid on EVERY compile because each `lake env lean` is a fresh
process. That is the thing to attack.

  ~20-45s   normal. Mathlib's oleans are cached and being read.
  ~90s+     the cache is probably cold or being rebuilt. `lake exe cache get`
            downloads prebuilt oleans and usually turns this into the case
            above — check that first, before touching any budget.
  ~300s+    something is reading Mathlib from disk very slowly. On Windows the
            usual cause is a virus scanner walking every .olean on each run;
            excluding the .lake directory is worth trying.

WHAT NO BUDGET CAN FIX
----------------------
If a compile costs 340s, an agent given eight compiles needs 45 minutes per
goal and a 20-goal benchmark takes 15 hours. Raising MRA_MAX_AGENT_SECONDS
buys the agent room but does not make the experiment affordable. The real fix
is to stop paying the import on every call — a persistent Lean process with
Mathlib already loaded, which is what LeanDojo and the Prover Agent line of
work use, and which turns 340s into well under a second per query.

That is a real piece of work. This script tells you whether you need it, or
whether `lake exe cache get` is enough.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

CASES = [
    ("empty file", ""),
    ("import Mathlib", "import Mathlib\n"),
    ("import + one theorem", "import Mathlib\ntheorem tmp_timing : 2 + 2 = 4 := by norm_num\n"),
]


def time_one(source: str, project: Path) -> tuple[float, bool]:
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "Timing.lean"
        path.write_text(source, encoding="utf-8")
        started = time.time()
        try:
            done = subprocess.run(
                ["lake", "env", "lean", str(path)],
                cwd=project, capture_output=True, text=True, timeout=1800,
            )
            return time.time() - started, done.returncode == 0
        except subprocess.TimeoutExpired:
            return time.time() - started, False
        except FileNotFoundError:
            print("`lake` is not on PATH. Is Lean installed and elan initialised?")
            raise SystemExit(2)


def main() -> int:
    project = Path(getattr(config, "LEAN_PROJECT", "") or ".")
    if not (project / "lakefile.lean").exists() and not (project / "lakefile.toml").exists():
        print(f"No lakefile in {project}. Set LEAN_PROJECT (config.py or .env)")
        print("to the Mathlib project directory, or run this from inside it.")
        return 2

    print(f"project: {project}")
    print("Each timing is a FRESH `lake env lean`, which is what the agent does.")
    print("Each case runs TWICE; the second (steady-state) reading is reported,")
    print("because a cold first read of Mathlib's oleans is not what the agent")
    print("sees after the first goal of a run.\n")

    timings = {}
    for label, source in CASES:
        cold, _ = time_one(source, project)          # warm-up, discarded
        warm, ok = time_one(source, project)
        timings[label] = warm
        status = "" if ok else "  (compile FAILED — the timing is still real)"
        print(f"  {label:<24} {warm:7.1f}s   (cold {cold:5.1f}s){status}")

    startup = timings["empty file"]
    compile_cost = timings["import + one theorem"]
    import_cost = compile_cost - startup

    print()
    print(f"  process + lake startup    {startup:7.1f}s")
    print(f"  Mathlib import            {import_cost:7.1f}s   paid on EVERY compile")
    print(f"  ------------------------------------")
    print(f"  ONE COMPILE               {compile_cost:7.1f}s")

    # Row 2 is reported but deliberately NOT used in the arithmetic: `import
    # Mathlib` with no declarations measures the same import as row 3 and is
    # kept only as a cross-check. If it is wildly different from row 3, the
    # machine is still not in a steady state and no number here is reliable.
    bare = timings["import Mathlib"]
    if abs(bare - compile_cost) > 0.5 * compile_cost:
        print(f"\n  WARNING: rows 2 and 3 disagree by more than half "
              f"({bare:.0f}s vs {compile_cost:.0f}s).")
        print("  The machine is not in a steady state — try again when idle.")

    print()
    if import_cost > 120:
        print("That is very slow. Before changing any budget, try:")
        print("    lake exe cache get        (downloads prebuilt .olean files)")
        print("and on Windows, exclude the .lake directory from virus scanning.")
        print("Then run this again.")
    elif import_cost > 30:
        print("Normal for a fresh process — and that is the problem, because it")
        print("is paid again on every compile. See docs/lean-overhead-diagnosis.md.")
    else:
        print("Healthy. The oleans are cached and being read normally.")

    overhead = (startup + import_cost) / compile_cost if compile_cost else 0
    print(f"\n{overhead:.0%} of every compile is fixed overhead, before Lean looks at")
    print("the proof at all. Eight compiles is roughly "
          f"{compile_cost * 8 / 60:.0f} minutes per goal,")
    print(f"of which about {(startup + import_cost) * 8 / 60:.0f} minutes is process "
          "startup and re-importing Mathlib.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
