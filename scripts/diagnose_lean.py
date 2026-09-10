"""Why can't this machine compile Lean? Prints the answer, does not guess.

    python scripts/diagnose_lean.py

WHY THIS EXISTS
---------------
`tests/test_lean_real.py` skips unless Mathlib is reachable, which is correct
-- a check that cannot run must skip rather than fail. But a skip whose reason
is "`import Mathlib` does not resolve" tells you the symptom and not the
cause, and there are at least five distinct causes: no `lean` on PATH, an elan
shim with no toolchain configured, `MRA_LEAN_PROJECT` unset, the variable set
to a path that is not a directory, `lake` missing, or the project not actually
depending on Mathlib.

MEASURED: the real-Lean tests were chased across three separate runs by
inference from a skip message, which is exactly the "measure, don't assume"
failure this repo keeps correcting elsewhere. This prints every input the
decision is made from, then the decision.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from verifiers.lean_runner import (  # noqa: E402
    LeanOutcome,
    lean_is_available,
    lean_toolchain_works,
    run_lean,
)


def line(label, value):
    print(f"  {label:<26} {value}")


def main() -> int:
    print("\nLEAN ENVIRONMENT")
    print("-" * 60)
    line("MRA_LEAN (command)", config.LEAN_COMMAND)
    line("on PATH", shutil.which(config.LEAN_COMMAND) or "NOT FOUND")
    line("lake on PATH", shutil.which("lake") or "NOT FOUND")
    line("MRA_LEAN_PROJECT", repr(config.LEAN_PROJECT))
    if config.LEAN_PROJECT:
        project = Path(config.LEAN_PROJECT)
        line("  is a directory", project.is_dir())
        line("  has lakefile", any(project.glob("lakefile.*")) if project.is_dir()
             else "n/a")
        line("  has lean-toolchain", (project / "lean-toolchain").is_file()
             if project.is_dir() else "n/a")
        line("  has .lake/packages", (project / ".lake" / "packages").is_dir()
             if project.is_dir() else "n/a")
    line("MRA_LEAN_TIMEOUT", config.LEAN_TIMEOUT)
    line("MRA_LEAN_COLD_TIMEOUT", config.LEAN_COLD_TIMEOUT)

    # THE BACKEND A REAL RUN USES, which this script did not report at all.
    #
    # MEASURED, and it cost five failed attempts at one verification run:
    # this printed "Mathlib is reachable" while `verify_results.py` returned
    # `unavailable` on every single proof. Both were right. The `import
    # Mathlib` probe below goes through `run_lean` -- the SUBPROCESS path --
    # and every recorded run of this agent used the REPL, selected by
    # MRA_LEAN_BACKEND and launched from MRA_LEAN_REPL_BIN. Neither variable
    # was printed here, neither is mentioned in docs/, and without the binary
    # `_repl.argv` falls back to `lake exe repl`, which cannot resolve
    # because the REPL is a separate project rather than a dependency of the
    # Mathlib workspace.
    #
    # A diagnostic that green-lights a configuration the run cannot use is
    # worse than none: it sends you looking somewhere else.
    print()
    line("MRA_LEAN_BACKEND", os.getenv("MRA_LEAN_BACKEND", "") or "(unset -> subprocess)")
    repl_bin = os.getenv("MRA_LEAN_REPL_BIN", "").strip()
    line("MRA_LEAN_REPL_BIN", repr(repl_bin) if repl_bin else "(unset)")
    backend_is_repl = os.getenv("MRA_LEAN_BACKEND", "").strip().lower() == "repl"
    if backend_is_repl:
        if repl_bin:
            line("  binary exists", Path(repl_bin).is_file())
        else:
            print("  NO BINARY SET, so `lake exe repl` is used -- which only")
            print("  works if the REPL is a dependency of MRA_LEAN_PROJECT.")
            print("  Set MRA_LEAN_REPL_BIN to a built repl if it is not.")
    elif repl_bin:
        print("  set, but MRA_LEAN_BACKEND is not `repl`, so it is unused.")

    print("\nWHAT `lean --version` SAYS")
    print("-" * 60)
    if shutil.which(config.LEAN_COMMAND):
        try:
            probe = subprocess.run([config.LEAN_COMMAND, "--version"],
                                   capture_output=True, text=True, timeout=60)
            line("returncode", probe.returncode)
            print(f"  stdout: {(probe.stdout or '').strip()[:300]}")
            print(f"  stderr: {(probe.stderr or '').strip()[:300]}")
        except Exception as exc:  # noqa: BLE001 - a diagnostic must not crash
            line("probe raised", exc)
    else:
        print("  skipped: lean is not on PATH")

    print("\nGATES")
    print("-" * 60)
    line("lean_is_available()", lean_is_available())
    line("lean_toolchain_works()", lean_toolchain_works())

    print("\nCOMPILING `import Mathlib`")
    print("-" * 60)
    print("  (this is the real probe; it may take a while)")
    result = run_lean("import Mathlib\n", timeout=config.LEAN_COLD_TIMEOUT)
    line("outcome", result.outcome.value)
    print("  output:")
    for text in (result.output or "(no output)").splitlines()[:20]:
        print(f"    {text}")

    print("\nVERDICT")
    print("-" * 60)
    if result.outcome is LeanOutcome.COMPILED:
        print("  Mathlib is reachable via the SUBPROCESS path, which is what")
        print("  this probe uses. tests/test_lean_real.py will RUN.")
        # NAMED, because the unqualified version of this line was true and
        # misleading at the same time. It said "Mathlib is reachable" while
        # `verify_results.py` returned `unavailable` on all 64 checkable
        # proofs, because the run used the REPL and this probe does not.
        if backend_is_repl:
            if repl_bin and Path(repl_bin).is_file():
                print()
                print("  MRA_LEAN_BACKEND=repl, and the binary above exists --")
                print("  but THIS PROBE DID NOT EXERCISE IT. A REPL that fails")
                print("  to start reports `unavailable` on every compile while")
                print("  everything here still passes. If a run says that,")
                print("  the REPL is the thing to look at, not this output.")
            else:
                print()
                print("  WARNING: MRA_LEAN_BACKEND=repl but no usable")
                print("  MRA_LEAN_REPL_BIN. Runs will try `lake exe repl` and")
                print("  report `unavailable` on every proof, even though")
                print("  every row above is healthy. MEASURED: this exact")
                print("  configuration produced 64 `unavailable` results.")
                return 1
        return 0

    if result.outcome is LeanOutcome.TIMEOUT:
        # MEASURED: this was the real answer on a machine where every other
        # row above was healthy. A timeout is not an absence, and the earlier
        # version of this script lumped it in with "the compile failed, read
        # the output" -- of which there was none, because a timeout produces
        # no output at all.
        print(f"  The compile TIMED OUT at {config.LEAN_COLD_TIMEOUT}s. That")
        print("  is not evidence Mathlib is missing -- a cold `import")
        print("  Mathlib` in a fresh subprocess is genuinely slow. Raise")
        print("  MRA_LEAN_COLD_TIMEOUT and try again.")
        return 1

    print("  Mathlib is NOT reachable, so those tests will skip. Likely fix,")
    print("  based on the rows above:")
    if not shutil.which(config.LEAN_COMMAND):
        print("    - install Lean, or set MRA_LEAN to its path")
    elif not lean_toolchain_works():
        print("    - no usable toolchain: run `elan default stable`, or run")
        print("      from inside a project with a lean-toolchain file")
    elif not config.LEAN_PROJECT:
        print("    - set MRA_LEAN_PROJECT to a Lake project depending on")
        print("      Mathlib (eval/results/*.json records the one used:")
        print("      look for `lean_project`)")
    elif not Path(config.LEAN_PROJECT).is_dir():
        print(f"    - MRA_LEAN_PROJECT points at {config.LEAN_PROJECT!r},")
        print("      which is not a directory")
    elif not shutil.which("lake"):
        print("    - `lake` is not on PATH, so the project cannot be entered")
    else:
        print("    - the project exists but the compile still failed; read the")
        print("      output above. If it mentions a missing package, run")
        print("      `lake exe cache get` then `lake build` in the project.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
