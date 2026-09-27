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
import signal
import subprocess

MODE =os.getenv("MRA_EXEC", "").strip().lower()

# TWO AXES, DELIBERATELY NOT ONE
# ------------------------------
#   MRA_EXEC          where a command runs   local | dispatch
#   MRA_LEAN_BACKEND  how Lean runs          subprocess | repl
#
# Overloading `MRA_EXEC` with a third value was considered and rejected.
# `enabled()` gates four unrelated things — the Lean path, the SymPy worker's
# interpreter, the SymPy worker's dispatch, and what the trace reports — so
# `MRA_EXEC=repl` would have silently sent every `check_numeric` through Aura
# and broken symbolic computation on a host that has no Aura. The axes are
# independent and are named independently.
SUBPROCESS = "subprocess"
REPL = "repl"


def lean_backend():
    """How Lean runs. Read at call time so tests and reloads see changes."""
    backend = os.getenv("MRA_LEAN_BACKEND", "").strip().lower()
    if backend in (SUBPROCESS, REPL):
        return backend
    # Backwards-compatible alias. Phase 2 shipped behind this flag and runs
    # recorded with it must stay reproducible.
    if os.getenv("MRA_LEAN_REPL", "").strip().lower() in ("1", "true", "yes"):
        return REPL
    return SUBPROCESS

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


# A TIMEOUT MUST KILL THE WHOLE TREE, NOT THE ONE PROCESS WE STARTED
# ------------------------------------------------------------------
# MEASURED in Aura, 2026-09-25: `lake env lean f.lean` is lake starting lean
# as its CHILD. `subprocess.run(timeout=)` kills only the process it started,
# so a timed-out compile killed lake and left lean running, reparented to
# init, still loading Mathlib at ~3 GB. The retry then competed with it for
# memory, timed out too, and left a second one: two orphans, 6 GB, on a 15 GB
# machine that was already swapping. So each command gets its own process
# group (a new session on POSIX, a new process group on Windows) and a timeout
# kills the group.
def _spawn_options():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_tree(process):
    """Kill `process` and everything it started. Never raises."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=30)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()      # in case the group kill could not reach it
    except OSError:
        pass


async def run(argv, workdir, stdin=None, timeout=180.0, cwd=None):
    """Run one command. Never raises — a failure is a Result, like a dispatch.

    On a timeout the command's whole process tree is killed before this
    returns (see `_kill_tree`), so nothing it started outlives the call.
    """
    import asyncio

    def call():
        try:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE if stdin is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd or LEAN_PROJECT or workdir,
                # Windows decoded Lean's UTF-8 output as cp1252 and crashed a
                # whole run. Bug 19; it must not come back through this path.
                encoding="utf-8",
                errors="replace",
                **_spawn_options(),
            )
        except (OSError, ValueError) as exc:
            return Result(False, -1, "", f"{type(exc).__name__}: {exc}")
        try:
            stdout, stderr = process.communicate(input=stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            try:
                process.communicate(timeout=10)   # reap, and close the pipes
            except (subprocess.SubprocessError, OSError, ValueError):
                pass
            return Result(False, -1, "", f"timed out after {timeout:.0f}s")
        return Result(
            process.returncode == 0,
            process.returncode,
            stdout or "",
            stderr or "",
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
        # `cwd=LEAN_PROJECT`, NOT the caller's directory. MEASURED: from
        # the repo root this call HANGS -- 0.8s inside the Lake project,
        # past 90s outside it. elan picks a toolchain from the nearest
        # `lean-toolchain` file, and with three toolchains installed and no
        # default set there is nothing to resolve outside a project, so it
        # blocks until the timeout. The probe then reported "lake is not
        # runnable" about a lake that runs fine, and `prover_spike` refused
        # to start three times on a working install.
        #
        # This is also the only honest place to run it: the question is
        # whether lake works FOR THIS PROJECT, and anywhere else does not
        # answer that.
        completed = subprocess.run(["lake", "--version"], capture_output=True,
                                   text=True, timeout=30, cwd=LEAN_PROJECT)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"lake is not runnable: {exc}"
    if completed.returncode != 0:
        return False, "lake --version failed"
    return True, ""
