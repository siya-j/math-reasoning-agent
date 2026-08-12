"""Dispatch helpers: how a math tool actually runs something.

NO `from __future__ import annotations` (§5.1, gotcha 1).

Two dispatchers, because this agent has two kinds of compute:

  lean_runner(workdir)    -> async (source) -> LeanResult
      Direct binary dispatch, the `gromacs_v2` shape. Writes the assembled
      .lean into the workspace (the only writable mount) and compiles it with
      `lake env lean` from inside the prebuilt Mathlib project — the only way
      `import Mathlib` resolves.

  worker_dispatch(workdir) -> async (op, args) -> envelope
      Op-registry RPC, the `builder_v2` shape. One `math_worker` module, op
      name in argv, arguments as JSON on stdin, so SymPy's import is paid once
      per call instead of once per operation.

Both return the SAME types the portable core in `math_v2/core/` already
expects, so nothing in core changes between a local subprocess and the SIF.
"""

import json
import os
import uuid

from verifiers.lean_runner import (
    LeanOutcome,
    LeanResult,
    cheating_devices,
    _uses_placeholder,
)

from math_v2 import _aura

# Where the agent's scratch .lean files go. Inside the workspace because that
# is the only writable mount (gotcha 11), and /tmp is discarded between the
# cold per-call execs.
SCRATCH = "math/lean"

WORKER = "math_worker"


def _classify(source, stdout, ok):
    """Turn compiler output into a LeanResult, reusing the existing rules.

    The anti-cheat runs HERE, on the way back, before anything reaches the
    model — `sorry`, `admit`, `axiom` and `exact?` all compile and prove
    nothing. It is not a prompt rule and must never become one.
    """
    cheats = cheating_devices(source)
    if ok and cheats:
        return LeanResult(LeanOutcome.CHEATED, ", ".join(cheats))
    if ok and _uses_placeholder(source, stdout):
        return LeanResult(LeanOutcome.INCOMPLETE, stdout)
    if ok:
        return LeanResult(LeanOutcome.COMPILED, stdout)
    return LeanResult(LeanOutcome.ERRORS, stdout)


def lean_runner(workdir):
    """An async `(source) -> LeanResult`, the seam `core/proving.py` injects."""

    async def run_lean(source):
        scratch = os.path.join(workdir, SCRATCH)
        os.makedirs(scratch, exist_ok=True)
        name = f"claim_{uuid.uuid4().hex[:8]}.lean"
        path = os.path.join(scratch, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)

        spec = _aura.command_spec(
            argv=["lake", "env", "lean", path],
            workdir=workdir,
            tool="lean",
        )
        try:
            result = await _aura.run(spec)
        except Exception as exc:  # noqa: BLE001 - a verifier never crashes the graph
            return LeanResult(LeanOutcome.UNAVAILABLE, f"Lean could not be run: {exc}")

        text = _aura.result_text(result)
        if not getattr(result, "ok", False) and not text.strip():
            text = _aura.failure_detail(result)
        return _classify(source, text, bool(getattr(result, "ok", False)))

    return run_lean


def worker_dispatch(workdir):
    """An async `(op, args) -> envelope`, the seam `core/symbolic.py` injects."""

    async def dispatch(op, args):
        spec = _aura.command_spec(
            argv=["python3", "-m", WORKER, op],
            workdir=workdir,
            tool=f"symbolic:{op}",
            stdin=json.dumps(args),
            timeout=60.0,
            memory_gb=2,
            cpus=1,
            sandbox_policy="strict",
        )
        try:
            result = await _aura.run(spec)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"the computation could not be run: {exc}"}

        text = _aura.result_text(result).strip()
        if not text:
            return {"ok": False, "error": _aura.failure_detail(result)}

        # The worker prints exactly one JSON line. Anything else means the
        # process died before it got there.
        try:
            return json.loads(text.splitlines()[-1])
        except ValueError:
            return {
                "ok": False,
                "error": f"the worker did not return JSON: {text[:400]}",
            }

    return dispatch


def stdin_unsupported():
    """True when the installed CommandSpec cannot carry stdin.

    The worker reads its arguments from stdin. If that field was dropped, every
    symbolic op would silently receive `{}` — so this is checked and reported
    rather than left to produce confidently wrong answers.
    """
    return "stdin" not in _aura.accepted_fields(_aura.CommandSpec)
