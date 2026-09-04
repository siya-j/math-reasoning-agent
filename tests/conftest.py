"""Make the suite independent of the operator's shell.

WHY THIS EXISTS
---------------
Six tests failed on the Windows machine and passed everywhere else. The cause
was not the code: the benchmark environment was still exported in that shell —

    MRA_EXEC=local  MRA_PROVER=math_v2  MRA_MAX_AGENT_SECONDS=300  ...

— and several modules read those variables at IMPORT time into module
constants. `_local.MODE` became "local", so tests that monkeypatch the
CommandSpec dispatch were silently exercising the subprocess path instead;
`config.PROVER` became "math_v2", so "the baseline is the default" was false.

A suite that only passes when the shell happens to be clean is not a safety
net. Anyone about to run a benchmark has exactly the environment that breaks
it, which is the worst possible time to lose the ability to check the code.

So: clear every MRA_* variable, then reload the modules that captured them, so
each run starts from the declared defaults. Tests that want a different value
monkeypatch it explicitly, which is visible in the test rather than in the
terminal history of whoever ran it.
"""

import importlib
import os

import pytest

# WHERE THE COMPILER IS, as opposed to WHAT THE CODE DOES. These two are
# exempt from the clearing below, and the distinction is the whole reason the
# exemption is safe: every variable the docstring blames --- MRA_EXEC,
# MRA_PROVER, MRA_MAX_AGENT_SECONDS --- changes the behaviour under test, so
# clearing it restores a declared default. These name a path on the machine.
# Clearing them cannot isolate a test; it can only disable one.
#
# MEASURED, and it disabled eleven. `tests/test_lean_real.py` skips unless
# `import Mathlib` resolves, which needs `MRA_LEAN_PROJECT` to point at a Lake
# project. This hook deleted it before collection, so the probe ran bare
# `lean`, failed with "unknown module prefix 'Mathlib'", and every real-Lean
# test skipped --- in a shell where the operator had just exported the
# variable specifically to run them. The only tests in this repo that touch a
# compiler could not run under the harness that runs them.
#
# Timeouts stay cleared: MRA_LEAN_TIMEOUT and MRA_LEAN_COLD_TIMEOUT change how
# long the code waits, which is behaviour, and their defaults are the ones the
# suite should be testing against.
_LOCATION_ONLY = frozenset({"MRA_LEAN", "MRA_LEAN_PROJECT"})


# Modules that read MRA_* into module-level constants at import time. Order
# matters: config first, then anything that reads from it.
_ENV_DEPENDENT = (
    "config",
    "math_v2._local",
    "math_v2._aura",
    "math_v2.core.budget",
)


def pytest_configure(config):
    """Runs before collection, so imports during collection see clean defaults.

    The original environment is stashed ON THE CONFIG OBJECT, not in a module
    global. A test reaching it via `from tests.conftest import ...` imports
    this file a SECOND time, as a different module object, and that copy is
    created AFTER the clearing below -- so a module-level capture records the
    cleared state and is worse than useless. `request.config` is the same
    object pytest already made.

    Why capture at all: without it a test cannot tell "the operator set
    nothing" from "the harness deleted it", and those want opposite responses
    -- a skip and a failure. A test that skips in both cases cannot catch the
    bug that produces the second.
    """
    config._original_mra_environment = {
        name: value for name, value in os.environ.items()
        if name.startswith("MRA_")
    }

    for name in list(os.environ):
        if name.startswith("MRA_") and name not in _LOCATION_ONLY:
            del os.environ[name]

    for name in _ENV_DEPENDENT:
        try:
            importlib.reload(importlib.import_module(name))
        except Exception:  # noqa: BLE001 - a missing optional module is fine
            pass


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """Belt and braces: a test that sets MRA_* cannot leak into the next one."""
    for name in list(os.environ):
        if name.startswith("MRA_") and name not in _LOCATION_ONLY:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_statement_preflight(monkeypatch):
    """The old prover's statement pre-flight is off unless a test asks for it.

    `pipeline/agentic_prover.prove` calls `ensure_elaborates` before anything
    else, and with no `statement_check` injected that reaches the REAL
    `run_lean`. The outcome then depends on the machine:

        no Lean at all      -> UNAVAILABLE -> "" -> the statement passes
        Lean but no Mathlib -> ERRORS      -> the statement FAILS

    A developer box has the first; a box set up to run benchmarks has the
    second, because `lean` is on PATH from elan while a bare `lean` cannot
    resolve `import Mathlib`. On that machine the prover returned before
    making a single attempt, so the budget and selector tests reported
    `attempts=[]` and never reached the behaviour they exist to check.

    Whether a statement elaborates is not what those tests are about, and a
    test whose result depends on what is installed is not a test. The
    statement-check tests re-enable it explicitly.
    """
    import config

    monkeypatch.setattr(config, "CHECK_STATEMENT", False)
