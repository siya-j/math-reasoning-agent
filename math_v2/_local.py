"""Local execution — the same commands, run as subprocesses instead of dispatched.

NO `from __future__ import annotations` (blueprint §5.1, gotcha 1).

WHY THIS IS NOT A SHORTCUT
--------------------------
AGENT_BLUEPRINT.md §7.2 provides exactly this for development:
`AURA_EXEC_BACKEND=<runtime>=local` "runs the command on the host with no
container". This is that path for `math_v2`, selected with `MRA_EXEC=local`.

It matters for three reasons beyond convenience:

  * The agent could not be evaluated at all without it. `_aura.command_spec`
    raises outside the Aura tree, so every Lean and SymPy tool failed.
  * It makes the 3 GB SIF optional for development, which is the difference
    between a ten-second edit-test loop and a container rebuild.
  * The argv it builds is the SAME argv the CommandSpec carries. If the two
    ever diverge, a local run stops predicting a dispatched one — so they are
    built from one place and there is a test asserting it.

WHAT IT DELIBERATELY DOES NOT REPRODUCE
---------------------------------------
Containment. A dispatched run executes inside math.sif with `--containall`;
this runs on the host with whatever Lean and Python are on PATH. That is
acceptable for evaluating our own goal set and is NOT acceptable for anything
user-supplied. `finish` reports which mode was used so a number can never be
quoted without knowing which one produced it.
"""

import os
import subprocess

MODE = os.getenv("MRA_EXEC", "").strip().lower()

# Where a local Lean run happens. `lake env lean` must run from inside a Lake
# project that depends on Mathlib — it is the only way `import Mathlib`
# resolves, however Mathlib is installed.
LEAN_PROJECT = os.getenv("MRA_LEAN_PROJECT", "") or os.getenv("LEAN_WORKSPACE", "")


def enabled():
    return MODE == "local"


class Result(object):
    """The subset of ExecutionResult that `_aura.result_text` reads.

    Deliberately duck-typed rather than importing Aura's class: the point of
    this module is to work where that class does not exist.
    """

    def __init__(self, ok, returncode, stdout, stderr):
        self.ok = ok
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_path = ""
        self.stderr_path = ""

    @property
    def outputs(self):
        return {}


async def run(argv, workdir, stdin=None, timeout=180.0, cwd=None):
    """Run one command. Never raises — a failure is a Result, like a dispatch."""
    import asyncio

    def call():
        try:
            completed = subprocess.run(
                list(argv),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd or LEAN_PROJECT or workdir,
                # Windows decoded Lean's UTF-8 output as cp1252 and crashed a
                # whole run. Bug 19; it must not come back through this path.
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return Result(False, -1, "", f"timed out after {timeout:.0f}s")
        except (OSError, ValueError) as exc:
            return Result(False, -1, "", f"{type(exc).__name__}: {exc}")
        return Result(
            completed.returncode == 0,
            completed.returncode,
            completed.stdout or "",
            completed.stderr or "",
        )

    return await asyncio.to_thread(call)


def lean_available():
    """Is there a Lake project with Mathlib to compile against?

    Reported rather than assumed: without it every goal is NOT PROVED for a
    reason that has nothing to do with the prover, and a run that silently
    scored 0% would be worse than one that refused to start.
    """
    if not LEAN_PROJECT:
        return False, "MRA_LEAN_PROJECT is not set to a Lake project with Mathlib"
    if not os.path.isdir(LEAN_PROJECT):
        return False, f"MRA_LEAN_PROJECT does not exist: {LEAN_PROJECT}"
    try:
        completed = subprocess.run(["lake", "--version"], capture_output=True,
                                   text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"lake is not runnable: {exc}"
    if completed.returncode != 0:
        return False, "lake --version failed"
    return True, ""
