"""The single seam between this package and the Aura framework.

NO `from __future__ import annotations` — tool modules import this (§5.1).

WHY EVERY AURA IMPORT IS IN ONE FILE
------------------------------------
Two of the interfaces this package depends on could not be verified. Rather
than scatter guesses across nine modules, every uncertain construction is made
here, once, so that correcting one is a single edit and the rest of the package
never has to change.

THE CONTRADICTION THIS FILE EXISTS TO SURVIVE
---------------------------------------------
`AGENT_BLUEPRINT.md` §7.2 shows a CommandSpec carrying `sandbox_policy`,
`timeout`, `resources=Resources(cpus=, memory_gb=, gpus=)`, and an
ExecutionResult carrying `file_changes`. The `core/command_spec.py` actually on
disk has none of them — it defines only runtime, workdir, argv, env, stdin,
metadata, user_id, and has no `Resources` class at all.

One of the two is stale and we do not know which. Passing a field the class
does not accept is a validation error on *every* dispatch; omitting one it does
accept costs a default. So `command_spec()` below asks the real class what it
accepts and passes only that, recording what it had to drop.

This is not defensive decoration. Mathlib needs 4-8 GB, and whether
`resources` exists decides whether we can ask for it — and per §7.2 anything
above 8 GB routes to SLURM instead of the ephemeral pool. If `resources` is
dropped, that request is silently not made, and `dropped_fields()` is how a
human finds out rather than discovering it in a latency graph.
"""

import dataclasses
import inspect

RUNTIME = "math"

# What we WANT to send. Filtered against reality at call time.
# WRONG WHEN WRITTEN, MEASURED SINCE. A Mathlib compile is not ~20s: the REPL
# session's own docstring measures a cold `import Mathlib` at 40.5s on the
# target machine and 116s cold on Windows (`_repl.START_TIMEOUT`), and
# subprocess-mode goals pay that on EVERY compile, not once — real runs
# recorded 55-300s per call (`eval/results/near-mathlib-subprocess.json`,
# `proofnet-4-after-goalstate.json`). 180s is still generous against a SINGLE
# call under that data, which is all this bounds; it is not generous against a
# cold REPL start, which is why that path gets its own longer
# `_repl.START_TIMEOUT` rather than reusing this one.
DEFAULT_TIMEOUT = 180.0
DEFAULT_MEMORY_GB = 8          # 8 keeps us on the ephemeral pool; >8 -> SLURM
DEFAULT_CPUS = 2

AVAILABLE = True
IMPORT_ERROR = ""

try:  # pragma: no cover - depends on the host repo
    from aura_framework.core.command_spec import CommandSpec
except Exception as exc:  # noqa: BLE001
    CommandSpec = None
    AVAILABLE = False
    IMPORT_ERROR = str(exc)

try:  # pragma: no cover
    from aura_framework.core.command_spec import Resources
except Exception:  # noqa: BLE001
    Resources = None            # absent in the version we were shown

try:  # pragma: no cover
    from aura_framework.core.backends import get_backend_for_runtime
except Exception:  # noqa: BLE001
    get_backend_for_runtime = None

try:  # pragma: no cover
    from aura_framework.core.command_spec import delegation_metadata
except Exception:  # noqa: BLE001
    def delegation_metadata():
        return {}


_dropped = set()


def accepted_fields(cls):
    """Field names a dataclass or pydantic model will actually accept."""
    if cls is None:
        return set()
    if dataclasses.is_dataclass(cls):
        return {f.name for f in dataclasses.fields(cls)}
    if hasattr(cls, "model_fields"):
        return set(cls.model_fields)
    if hasattr(cls, "__fields__"):                     # pydantic v1
        return set(cls.__fields__)
    try:
        return set(inspect.signature(cls).parameters) - {"self"}
    except (TypeError, ValueError):
        return set()


def dropped_fields():
    """Fields we wanted to send and the installed CommandSpec refused.

    Surfaced by `finish` and worth reading: a dropped `timeout` means a runaway
    compile is bounded by something other than us.
    """
    return sorted(_dropped)


def command_spec(*, argv, workdir, tool, timeout=DEFAULT_TIMEOUT,
                 memory_gb=DEFAULT_MEMORY_GB, cpus=DEFAULT_CPUS, stdin=None,
                 sandbox_policy="compute", env=None):
    """Build a CommandSpec using only the fields this installation has."""
    if CommandSpec is None:
        raise RuntimeError(
            "aura_framework.core.command_spec is not importable: " + IMPORT_ERROR
        )

    wanted = {
        "runtime": RUNTIME,
        "workdir": workdir,
        "argv": list(argv),
        "env": dict(env or {}),
        "metadata": {"tool": tool, **delegation_metadata()},
        "stdin": stdin,
        "timeout": timeout,
        "sandbox_policy": sandbox_policy,
    }
    if Resources is not None:
        wanted["resources"] = Resources(cpus=cpus, memory_gb=memory_gb, gpus=0)
    else:
        # No Resources class in this installation, so the memory request cannot
        # be expressed at all. Recorded as dropped because the EFFECT is the
        # same as the field being refused: Mathlib's 4-8 GB was never asked for.
        _dropped.add("resources")

    allowed = accepted_fields(CommandSpec)
    _dropped.update(name for name in wanted if name not in allowed)

    return CommandSpec(**{k: v for k, v in wanted.items() if k in allowed})


async def run(spec):
    """Dispatch a spec and return its ExecutionResult."""
    if get_backend_for_runtime is None:
        raise RuntimeError("aura_framework.core.backends is not importable")
    return await get_backend_for_runtime(RUNTIME).arun(spec)


def result_text(result):
    """Best-effort stdout for an ExecutionResult, whichever shape it has.

    The version we were shown carries `stdout_path` rather than the text, so
    the file is read when a path is what we get. `outputs` is preferred when
    the backend already parsed something.
    """
    for attribute in ("stdout", "output"):
        value = getattr(result, attribute, None)
        if isinstance(value, str) and value:
            return value

    path = getattr(result, "stdout_path", "")
    if path:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            pass
    return ""


def failure_detail(result):
    """What to tell the model when a dispatch failed.

    §7.2: always surface `stderr_path` on failure — it is how the agent debugs
    itself. Include the text when it is cheap to read.
    """
    parts = []
    code = getattr(result, "returncode", None)
    if code is not None:
        parts.append(f"exit code {code}")

    path = getattr(result, "stderr_path", "")
    if path:
        parts.append(f"stderr: {path}")
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                tail = handle.read()[-1500:]
            if tail.strip():
                parts.append(tail.strip())
        except OSError:
            pass
    return "\n".join(parts) or "the command failed with no diagnostic output"
