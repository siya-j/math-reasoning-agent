"""Does this install work? One command, and it cannot lie about the answer.

WHY A COMMAND AND NOT A README
------------------------------
This repo has a 215-line README and eleven files in docs/. The setup it needs
is spread across them and two pieces are in neither:

    MRA_PROVER, MRA_EXEC                       docs/RUN_NEXT.md
    MRA_LEAN, MRA_LEAN_PROJECT, MRA_LEAN_*     docs/HANDOFF.md
    MRA_LEAN_BACKEND, MRA_LEAN_REPL_BIN        nowhere

MEASURED, over five failed attempts at a single verification run: commands
were assembled from those docs with one variable, then two, then three, and
the fourth (`MRA_EXEC`) was found only by tracing an exception. Meanwhile
`diagnose_lean.py` reported "Mathlib is reachable" throughout, because its
probe uses the SUBPROCESS path and every real run uses the REPL.

Documentation cannot check itself. A list of four variables in prose is four
chances to be wrong, and this project's own recurring lesson is that a rule
living only in prose is one that gets declined -- by a model reading a
prompt, and equally by a person reading setup instructions.

WHAT IT CHECKS, AND WHY EACH ONE
--------------------------------
Every row is something that has actually broken, not a guess:

  * the four environment variables a run reads, with what each does when
    unset rather than just whether it is set;
  * that `lean` and `lake` resolve, and that the toolchain can COMPILE --
    `lean --version` exits 0 under an unconfigured elan, so exit codes lie;
  * the REPL binary, when the REPL backend is selected. Without it
    `_repl.argv` falls back to `lake exe repl`, which cannot resolve when the
    REPL is a separate project;
  * BOTH COMPILE PATHS, separately. `import Mathlib` through the subprocess
    arm and a real theorem through whichever backend is configured. A pass on
    one says nothing about the other, which is exactly how a healthy-looking
    environment produced 64 identical failures.

    python scripts/check_install.py
    python scripts/check_install.py --quick     # skip the compiles
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = "  ok  "
BAD = " FAIL "
WARN = " warn "


class Report(object):
    """Rows, and whether anything fatal was found."""

    def __init__(self):
        self.fatal = 0
        self.warned = 0

    def row(self, mark, label, detail=""):
        print(f"{mark}  {label:34}{detail}")
        if mark is BAD:
            self.fatal += 1
        elif mark is WARN:
            self.warned += 1

    def note(self, text):
        for line in text.splitlines():
            print(f"        {line}")


def environment(report):
    print("\nENVIRONMENT")
    print("-" * 68)
    import config

    prover = os.getenv("MRA_PROVER", "")
    report.row(OK if prover else WARN, "MRA_PROVER",
               prover or "(unset -> 'pipeline', the BASELINE prover)")
    if not prover:
        report.note("Set MRA_PROVER=math_v2 to run the agent under development.\n"
                    "Recorded in every results file, so a run is never\n"
                    "mislabelled -- but nothing warns you at the start.")

    mode = os.getenv("MRA_EXEC", "")
    report.row(OK if mode == "local" else WARN, "MRA_EXEC",
               mode or "(unset -> dispatch to Aura)")
    if mode != "local":
        report.note("Unset, any source needing a fresh `lake env lean` is\n"
                    "routed to aura_framework, which is not installed here.\n"
                    "MEASURED: that is `import Mathlib.Topology.Basic` in a\n"
                    "statement failing as `unavailable`. Set MRA_EXEC=local.")

    project = config.LEAN_PROJECT
    report.row(OK if project and Path(project).is_dir() else BAD,
               "MRA_LEAN_PROJECT", project or "(unset)")
    if not project:
        report.note("A Lake project that depends on Mathlib. Without it\n"
                    "`import Mathlib` cannot resolve however Mathlib is\n"
                    "installed.")
    elif not Path(project).is_dir():
        report.note("Set, but not a directory.")
    else:
        for name, probe in (("lakefile", any(Path(project).glob("lakefile.*"))),
                            ("lean-toolchain",
                             (Path(project) / "lean-toolchain").is_file()),
                            (".lake/packages",
                             (Path(project) / ".lake" / "packages").is_dir())):
            report.row(OK if probe else BAD, f"  has {name}", str(probe))

    backend = os.getenv("MRA_LEAN_BACKEND", "").strip().lower()
    report.row(OK, "MRA_LEAN_BACKEND", backend or "(unset -> subprocess)")
    binary = os.getenv("MRA_LEAN_REPL_BIN", "").strip()
    if backend == "repl":
        exists = bool(binary) and Path(binary).is_file()
        report.row(OK if exists else BAD, "MRA_LEAN_REPL_BIN",
                   binary or "(unset)")
        if not exists:
            report.note("The REPL backend is selected. Without a built repl\n"
                        "binary `lake exe repl` is used, which only resolves\n"
                        "if the REPL is a dependency of MRA_LEAN_PROJECT.\n"
                        "MEASURED: this exact state returned `unavailable` on\n"
                        "all 64 checkable proofs while every other row passed.")
    elif binary:
        report.row(WARN, "MRA_LEAN_REPL_BIN", "set but backend is not `repl`")


def toolchain(report):
    print("\nTOOLCHAIN")
    print("-" * 68)
    import config
    from verifiers.lean_runner import lean_is_available, lean_toolchain_works

    report.row(OK if shutil.which(config.LEAN_COMMAND) else BAD,
               "lean on PATH", shutil.which(config.LEAN_COMMAND) or "NOT FOUND")
    report.row(OK if shutil.which("lake") else BAD,
               "lake on PATH", shutil.which("lake") or "NOT FOUND")
    report.row(OK if lean_is_available() else BAD,
               "lean_is_available()", str(lean_is_available()))
    works = lean_toolchain_works()
    report.row(OK if works else BAD, "lean_toolchain_works()", str(works))
    if not works:
        report.note("`lean --version` exits 0 under an unconfigured elan, so\n"
                    "this reads the version banner instead of the exit code.\n"
                    "Fix: `elan default stable`, or run from a project with a\n"
                    "lean-toolchain file.")


def compiles(report):
    """BOTH PATHS. A pass on one says nothing about the other."""
    print("\nCOMPILING (the slow part -- a cold `import Mathlib`)")
    print("-" * 68)
    import config
    from verifiers.lean_runner import LeanOutcome, run_lean

    print("        subprocess path ...", flush=True)
    result = run_lean("import Mathlib\n", timeout=config.LEAN_COLD_TIMEOUT)
    good = result.outcome is LeanOutcome.COMPILED
    report.row(OK if good else BAD, "subprocess: import Mathlib",
               result.outcome.value)
    if not good:
        report.note((result.output or "no output").strip()[:300])

    backend = os.getenv("MRA_LEAN_BACKEND", "").strip().lower()
    if backend != "repl":
        report.row(WARN, "REPL path", "not selected, so not exercised")
        report.note("Every recorded run of this agent used the REPL. If you\n"
                    "intend to reproduce one, set MRA_LEAN_BACKEND=repl and\n"
                    "run this again -- the paths fail independently.")
        return

    print("        REPL path ...", flush=True)
    import asyncio

    from math_v2.tools import _util

    async def once():
        return await _util.lean_runner(".")(
            "import Mathlib\n\ntheorem check_install : 1 + 1 = 2 := by norm_num\n")

    try:
        repl_result = asyncio.run(once())
    except Exception as exc:            # noqa: BLE001 - a check must not crash
        report.row(BAD, "REPL: a real theorem", f"{type(exc).__name__}")
        report.note(str(exc)[:300])
        return
    good = repl_result.outcome is LeanOutcome.COMPILED
    report.row(OK if good else BAD, "REPL: a real theorem",
               repl_result.outcome.value)
    if not good:
        report.note((repl_result.output or "no output").strip()[:300])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--quick", action="store_true",
                        help="skip the compiles, which are the slow part")
    args = parser.parse_args(argv)

    report = Report()
    environment(report)
    toolchain(report)
    if not args.quick:
        compiles(report)
    else:
        print("\n(--quick: no compile was attempted, so nothing here shows "
              "whether\nLean can actually build anything.)")

    print("\n" + "=" * 68)
    if report.fatal:
        print(f"  {report.fatal} FATAL problem(s). This install cannot run the "
              f"agent.")
        print("  Every FAIL row above says what to set and why.")
        return 1
    if report.warned:
        print(f"  usable, with {report.warned} warning(s) -- read them: a "
              f"warning here")
        print("  means a run will WORK but not do what you probably intend.")
        return 0
    print("  this install works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
