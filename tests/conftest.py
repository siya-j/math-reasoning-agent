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

# Modules that read MRA_* into module-level constants at import time. Order
# matters: config first, then anything that reads from it.
_ENV_DEPENDENT = (
    "config",
    "math_v2._local",
    "math_v2._aura",
    "math_v2.core.budget",
)


def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    """Runs before collection, so imports during collection see clean defaults."""
    for name in list(os.environ):
        if name.startswith("MRA_"):
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
        if name.startswith("MRA_"):
            monkeypatch.delenv(name, raising=False)
