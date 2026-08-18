"""Dispatch helpers: how a math tool actually runs something.

NO `from __future__ import annotations` (§5.1, gotcha 1).

Two dispatchers, because this agent has two kinds of compute:

  lean_runner(workdir)    -> async (source) -> LeanResult
      Direct binary dispatch, the `gromacs_v2` shape. Writes the assembled
      .lean into the workspace (the only writable mount) and compiles it with
      `lake env lean` from inside the prebuilt Mathlib project — the only way
      `import Mathlib` resolves.

      With MRA_LEAN_REPL=1 the compile goes to a persistent Lean process
      instead (`_repl.py`), which imports Mathlib once rather than on every
      call. That is the ONLY thing the flag changes: both paths return
      `(ok, text)` and are classified, memoised and timed by the same code
      below, so the anti-cheat and the outcome vocabulary cannot diverge
      between them.

  worker_dispatch(workdir) -> async (op, args) -> envelope
      Op-registry RPC, the `builder_v2` shape. One `math_worker` module, op
      name in argv, arguments as JSON on stdin, so SymPy's import is paid once
      per call instead of once per operation.

Both return the SAME types the portable core in `math_v2/core/` already
expects, so nothing in core changes between a local subprocess and the SIF.
"""

import json
import os
import time
import uuid

from verifiers.lean_runner import (
    LeanOutcome,
    LeanResult,
    cheating_devices,
    _uses_placeholder,
)

from math_v2 import _aura, _local
from math_v2.core import budget
from math_v2.tools import _repl

# Where the agent's scratch .lean files go. Inside the workspace because that
# is the only writable mount (gotcha 11), and /tmp is discarded between the
# cold per-call execs.
SCRATCH = "math/lean"

WORKER = "math_worker"


def lean_argv(path):
    """The command that compiles one file. ONE definition, both execution modes.

    `lake env lean` from inside the Mathlib project is the only way a bare
    `import Mathlib` resolves. If local and dispatched ever built different
    argv, a local run would stop predicting a dispatched one.
    """
    return ["lake", "env", "lean", path]


def worker_argv(op, python=None):
    """The command that runs one symbolic operation.

    The interpreter differs between the two modes and cannot not: inside the
    SIF `python3` is on PATH by construction (%environment puts /opt/python/bin
    there), while on a host it may not exist at all — Windows typically has
    `python` or the py launcher and no `python3`. That is what made the local
    worker fail there while every mocked test passed.

    Everything after the interpreter is identical in both modes, and there is
    a test asserting it.
    """
    return [python or _interpreter(), "-m", WORKER, op]


def _interpreter():
    """The interpreter to run the worker with.

    Locally, the one running this process — guaranteed to exist and to be the
    same Python the tests import the worker with. In the SIF, `python3`, which
    resolves to the baked venv.
    """
    if _local.enabled():
        import sys

        return sys.executable
    return "python3"


def mode():
    """WHERE commands run. Reported by `finish` and written into the trace."""
    return "local" if _local.enabled() else "dispatch"


def lean_backend():
    """HOW Lean runs. A separate axis from `mode()` and reported separately:
    `local+repl` would read as a third execution mode, and it is not one."""
    return _local.lean_backend()


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
    """An async `(source) -> LeanResult`, the seam `core/proving.py` injects.

    ALSO where a compile is TIMED. Every Lean invocation in the agent passes
    through here, so this is the one place that can tell the budget what a
    compile actually costs on this machine — which is how the reserve stops
    being a constant guessed on someone else's hardware. Measured failure:
    1032s against a 300s budget, three compiles at ~340s each, 60s reserved.
    """

    async def run_lean(source):
        cached = _memo.get((workdir, _digest(source)))
        if cached is not None:
            return cached

        started = time.time()
        # A list because `finally` may run before the assignment below — an
        # exception on the very first compile would otherwise leave the name
        # unbound and turn a Lean failure into a NameError.
        _startup = [0.0]
        try:
            # THE ONLY DIFFERENCE BETWEEN THE TWO EXECUTION PATHS. Both produce
            # `(ok, text)` and everything after this — the anti-cheat, the
            # outcome vocabulary, the memo, the timing — is shared, so a
            # divergence in behaviour between them can only come from Lean
            # itself and not from our bookkeeping.
            if _repl.enabled():
                ok, text, _startup[0] = await _repl.compile_source(
                    source, cwd=_local.LEAN_PROJECT or workdir)
            else:
                ok, text, _startup[0] = await _subprocess_compile(source, workdir)
        except Exception as exc:  # noqa: BLE001 - a verifier never crashes the graph
            return LeanResult(LeanOutcome.UNAVAILABLE, f"Lean could not be run: {exc}")
        finally:
            # In `finally` deliberately: a compile that timed out or died is
            # still evidence of what this machine costs, and it is the SLOW
            # ones the reserve exists to survive. Recording only successes
            # would learn the cheapest number and keep the bug.
            #
            # MINUS `startup`, which is the REPL's one-time Mathlib import and
            # not the cost of a compile. Including it would teach the reserve
            # that a compile costs 35s when it costs 0.2s, and a quarter of
            # every budget would be held back for the rest of the run. The
            # wall clock is unaffected: it is measured from `budget.reset` and
            # never from this, so startup still counts against the deadline.
            budget.record_lean_seconds(workdir, time.time() - started - _startup[0])

        outcome = _classify(source, text, ok)
        _remember(workdir, source, outcome)
        return outcome

    return run_lean


async def _subprocess_compile(source, workdir):
    """One fresh `lake env lean`. The default path, unchanged."""
    scratch = os.path.join(workdir, SCRATCH)
    os.makedirs(scratch, exist_ok=True)
    name = f"claim_{uuid.uuid4().hex[:8]}.lean"
    path = os.path.join(scratch, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(source)

    argv = lean_argv(path)
    if _local.enabled():
        result = await _local.run(argv, workdir, timeout=_aura.DEFAULT_TIMEOUT)
    else:
        result = await _aura.run(_aura.command_spec(
            argv=argv, workdir=workdir, tool="lean"))

    ok = bool(getattr(result, "ok", False))
    text = _aura.result_text(result)
    if not ok and not text.strip():
        text = _aura.failure_detail(result)
    # The triple is shared with the REPL path. A fresh process has no
    # amortised startup to separate out — every second of it IS the compile.
    return ok, text, 0.0


# Identical source compiled twice for one goal. Lean is deterministic, so the
# second compile can only produce the same answer — and at ~45s each, of which
# ~35s is re-importing Mathlib, that is a third of a 300s budget thrown away.
# Keyed by workdir, and a workdir is created per goal, so the cache lives
# exactly as long as the goal does.
_memo = {}

# Cheaper than it looks, and worth stating: only VERDICTS are cached. A
# compile that timed out, or found no compiler at all, says nothing about the
# source and everything about the moment — caching those would turn one
# transient failure into a permanent one for the rest of the goal.
_CACHEABLE = (LeanOutcome.COMPILED, LeanOutcome.INCOMPLETE,
              LeanOutcome.CHEATED, LeanOutcome.ERRORS)


def _digest(source):
    import hashlib

    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _remember(workdir, source, result):
    if result.outcome in _CACHEABLE:
        _memo[(workdir, _digest(source))] = result


def forget(workdir=None):
    """Drop cached compiles. Called when a goal starts; all of it if None."""
    if workdir is None:
        _memo.clear()
        return
    for key in [k for k in _memo if k[0] == workdir]:
        del _memo[key]


def worker_dispatch(workdir):
    """An async `(op, args) -> envelope`, the seam `core/symbolic.py` injects."""

    async def dispatch(op, args):
        argv = worker_argv(op)
        payload = json.dumps(args)
        try:
            if _local.enabled():
                result = await _local.run(argv, workdir, stdin=payload,
                                          timeout=60.0, cwd=_worker_dir())
            else:
                result = await _aura.run(_aura.command_spec(
                    argv=argv, workdir=workdir, tool=f"symbolic:{op}",
                    stdin=payload, timeout=60.0, memory_gb=2, cpus=1,
                    sandbox_policy="strict"))
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


def _worker_dir():
    """Where `python3 -m math_worker` resolves locally.

    In the SIF the module is on PYTHONPATH via %environment; on a host it is
    found by running from the scripts directory.
    """
    import os

    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "subagents", "math", "scripts",
    )


def stdin_unsupported():
    """True when the installed CommandSpec cannot carry stdin.

    The worker reads its arguments from stdin. If that field was dropped, every
    symbolic op would silently receive `{}` — so this is checked and reported
    rather than left to produce confidently wrong answers.
    """
    if _local.enabled():
        return False        # a subprocess always has stdin
    return "stdin" not in _aura.accepted_fields(_aura.CommandSpec)
