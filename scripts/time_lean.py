"""How long does one Lean compile actually cost on THIS machine?

    python scripts/time_lean.py

WHY THIS EXISTS
---------------
Two budget bugs in a row came from a constant guessed on different hardware.
The reserve assumed a compile costs 60s; on Windows a statement check took
roughly 340s, so exercise_1_19b spent 1032s against a 300s budget. The budget
now measures this itself at run time, but before choosing a budget at all it is
worth knowing the number — and, more importantly, WHY it is what it is.

WHAT THE THREE TIMINGS MEAN

  1. empty file           process startup, Lake resolution. No Mathlib.
  2. `import Mathlib`     the whole library loaded from .olean files.
  3. a trivial theorem    the same import, plus elaborating one line.

The gap between 1 and 2 is the entire cost, and it is paid on EVERY compile
because each `lake env lean` is a fresh process. That is the thing to attack.

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
    print("Each timing is a FRESH `lake env lean`, which is what the agent does.\n")

    timings = {}
    for label, source in CASES:
        seconds, ok = time_one(source, project)
        timings[label] = seconds
        status = "" if ok else "   (compile FAILED — the timing is still real)"
        print(f"  {label:<24} {seconds:7.1f}s{status}")

    import_cost = timings["import Mathlib"] - timings["empty file"]
    print(f"\n  cost of importing Mathlib {import_cost:7.1f}s   paid on EVERY compile")

    print()
    if import_cost > 120:
        print("That is very slow. Before changing any budget, try:")
        print("    lake exe cache get        (downloads prebuilt .olean files)")
        print("and on Windows, exclude the .lake directory from virus scanning.")
        print("Then run this again.")
    elif import_cost > 60:
        print("Slow but workable. `lake exe cache get` may still help.")
    else:
        print("Healthy. The oleans are cached and being read normally.")

    slowest = timings["import + one theorem"]
    print(f"\nFor a budget: one compile costs about {slowest:.0f}s here, so N compiles")
    print(f"need at least {slowest:.0f}N seconds plus model latency. Eight compiles")
    print(f"is roughly {slowest * 8 / 60:.0f} minutes per goal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
