"""`--budget-profile hard-reasoning` — a real step up, not SOTA's scale.

WHY THIS EXISTS
---------------
`math_v2/core/budget.py`'s constants are module-level globals read ONCE via
`os.getenv` at first import — there is no function anywhere that re-reads
them per goal or per tier. `BENCHMARK_2026_08` already sets the precedent for
a NAMED profile that is nonetheless "applied by setting the environment,
never by changing the defaults" — a human copies its values into a shell.
`pipeline.proving.HARD_REASONING` is the same shape of dict, but
`--budget-profile` makes it actually applicable from the CLI, via
`os.environ.setdefault` BEFORE the first `prove()` call.

ROUTED THROUGH `pipeline.proving.budget_profile`, NOT A DIRECT `math_v2`
IMPORT — the same seam `environment()`/`prove()` already use, so
`scripts/evaluate_proofs.py` stays prover-agnostic
(`tests/test_mathv2_integration.py::test_evaluate_proofs_never_imports_a_prover_directly`
forbids the literal substring `math_v2` there). One real consequence: the
profile only applies when `config.PROVER == "math_v2"` — its env var names
(`MRA_MAX_AGENT_LEAN` etc.) are math_v2's own and have no effect on any other
configured prover, so `budget_profile` returns `{}` and touches nothing on
purpose in that case, rather than silently setting variables nobody reads.

That last sentence was FALSE when it was written, which is why it is now
pinned by a test below. `config.py` read the same four `MRA_MAX_AGENT_*`
names for the AGENTIC prover, with different defaults (8 vs 12 Lean calls,
20 vs 40 steps, 8 vs 12 searches, 300 vs 900 seconds). Nothing miscounted —
one prover runs per process and `budget_profile` never applied under the
agentic one — but the claim above was not true of the codebase. The agentic
bounds now read `MRA_AGENTIC_*` first and fall back to the old names, so the
claim holds and an existing export still does what it always did.

THE VALUES LIVE IN `pipeline.proving`, NOT `math_v2.core.budget`, DESPITE
BEING MATH_V2-SPECIFIC. MEASURED FAILURE, shipped and caught on a real run:
`budget_profile` used to do `from math_v2.core import budget` to read the
profile dict off it, and that import itself executes `budget.py`'s own
`os.getenv` reads immediately — before this function ever reached its own
`os.environ.setdefault` loop. `os.environ` ended up holding the right
values; `math_v2.core.budget`'s already-computed constants did not. A real
PutnamBench run printed the profile's own banner while every goal still ran
under the OLD 12-compile default. `test_the_profile_actually_reaches_
budget_py_constants` below runs the real sequence in a fresh subprocess —
the only way to see `math_v2.core.budget` import for the first time, which
is exactly the condition the bug needed and an in-process test using an
already-imported module could never reproduce.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402
from evaluate_proofs import apply_budget_profile  # noqa: E402
from pipeline.proving import HARD_REASONING  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_hard_reasoning_profile_values_are_present():
    assert HARD_REASONING == {
        "MRA_MAX_AGENT_SECONDS": "3600",
        "MRA_MAX_AGENT_LEAN": "40",
        "MRA_MAX_AGENT_STEPS": "120",
        "MRA_MAX_AGENT_SEARCHES": "40",
        "MRA_MAX_CONSECUTIVE_SEARCHES": "3",
    }


def test_hard_reasoning_max_lean_calls_clears_the_measured_overrun():
    """MEASURED: `hard-irrational-sqrt-sum` needed more than 12 real compiles
    to get from "every needed lemma proved" to "assembled" -- it overran the
    default (12) even past docs/hard-tier-candidates.md's own 5-8 estimate.
    The profile must clear that with real margin, not just barely."""
    assert int(HARD_REASONING["MRA_MAX_AGENT_LEAN"]) > 12


def test_applying_the_profile_sets_environment_defaults(monkeypatch):
    monkeypatch.setattr(config, "PROVER", "math_v2")
    for key in HARD_REASONING:
        monkeypatch.delenv(key, raising=False)

    applied = apply_budget_profile("hard-reasoning")

    assert applied == HARD_REASONING
    for key, value in HARD_REASONING.items():
        assert os.environ[key] == value


def test_an_explicit_env_var_is_not_overridden_by_the_profile(monkeypatch):
    """setdefault, not assignment -- a convenience default, never a forced
    override. An env var the user already exported must still win."""
    monkeypatch.setattr(config, "PROVER", "math_v2")
    monkeypatch.setenv("MRA_MAX_AGENT_SECONDS", "42")

    apply_budget_profile("hard-reasoning")

    assert os.environ["MRA_MAX_AGENT_SECONDS"] == "42"


def test_the_profile_applies_nothing_under_a_different_prover(monkeypatch):
    """`MRA_MAX_AGENT_LEAN` etc. are math_v2's own env var names -- no other
    configured prover reads them, so applying them would silently set
    variables nobody looks at rather than doing the honest thing: nothing."""
    monkeypatch.setattr(config, "PROVER", "pipeline")
    for key in HARD_REASONING:
        monkeypatch.delenv(key, raising=False)

    applied = apply_budget_profile("hard-reasoning")

    assert applied == {}
    for key in HARD_REASONING:
        assert key not in os.environ


def test_the_agentic_bounds_do_not_read_math_v2s_env_var_names():
    """The claim in this module's docstring, checked against the source
    rather than trusted. `MRA_MAX_AGENT_*` may appear in `config.py` ONLY as
    a fallback beside an `MRA_AGENTIC_*` name -- never as the sole reader,
    which is what made the claim false before."""
    source = (ROOT / "config.py").read_text(encoding="utf-8")

    for suffix, agentic in (("STEPS", "MRA_AGENTIC_STEPS"),
                            ("LEAN", "MRA_AGENTIC_LEAN"),
                            ("SEARCHES", "MRA_AGENTIC_SEARCHES"),
                            ("SECONDS", "MRA_AGENTIC_SECONDS")):
        shared = f"MRA_MAX_AGENT_{suffix}"
        for line_no, line in enumerate(source.splitlines()):
            if f'os.getenv("{shared}"' not in line:
                continue
            window = "\n".join(source.splitlines()[max(0, line_no - 3):line_no + 1])
            assert f'os.getenv("{agentic}")' in window, (
                f"{shared} is read in config.py without {agentic} in front "
                "of it -- the two provers share a name again"
            )


@pytest.fixture
def reloaded_config(monkeypatch):
    """`config.py` reads its bounds at IMPORT time, so seeing an env var take
    effect means reloading the module -- and reloading it MUTATES the shared
    module object every later test holds. `conftest` restores the environment
    between tests but not the modules it reloaded once at `pytest_configure`,
    so without the teardown below the next test to read `config` gets
    whatever the last one set. That is precisely the cross-test leak conftest
    exists to prevent.
    """
    import importlib

    def reload(**environment):
        for key in ("MRA_AGENTIC_LEAN", "MRA_MAX_AGENT_LEAN"):
            monkeypatch.delenv(key, raising=False)
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield reload

    for key in ("MRA_AGENTIC_LEAN", "MRA_MAX_AGENT_LEAN"):
        monkeypatch.delenv(key, raising=False)
    importlib.reload(config)


def test_an_agentic_specific_bound_wins_over_the_shared_name(reloaded_config):
    reloaded = reloaded_config(MRA_MAX_AGENT_LEAN="40", MRA_AGENTIC_LEAN="5")

    assert reloaded.MAX_AGENT_LEAN_CALLS == 5


def test_the_old_shared_name_still_bounds_an_agentic_run(reloaded_config):
    """Compatibility is the whole reason the fallback exists. Exporting
    `MRA_MAX_AGENT_LEAN` to bound an agentic run is a thing that has been
    done, and ignoring it would be a worse failure than the ambiguity."""
    reloaded = reloaded_config(MRA_MAX_AGENT_LEAN="40")

    assert reloaded.MAX_AGENT_LEAN_CALLS == 40


def test_the_two_provers_defaults_still_differ_as_designed(reloaded_config):
    """Not a tidy-up: the defaults are meant to differ, which is exactly why
    one env var could not serve both. If they ever converge, the fallback
    above stops being a compromise and this test should be deleted."""
    from math_v2.core import budget

    assert reloaded_config().MAX_AGENT_LEAN_CALLS == 8
    assert budget.MAX_LEAN_CALLS == 12


def test_the_budget_profile_flag_is_wired_into_the_parser():
    source = Path("scripts/evaluate_proofs.py").read_text(encoding="utf-8")

    assert '"--budget-profile"' in source
    assert 'choices=("hard-reasoning",)' in source


def test_the_profile_does_not_import_math_v2_core_budget_to_read_itself():
    """THE exact regression: reading the profile's own values by importing
    `math_v2.core.budget` would re-trigger the bug this file documents. This
    checks the fix is structural, not merely value-drift-tested."""
    source = (ROOT / "pipeline" / "proving.py").read_text(encoding="utf-8")
    start = source.index("def budget_profile")
    end = source.index("\ndef ", start + 1)
    profile_fn = source[start:end]

    assert "from math_v2" not in profile_fn


def test_the_profile_actually_reaches_budget_py_constants():
    """THE end-to-end regression. Run in a fresh subprocess so
    `math_v2.core.budget` genuinely imports for the first time, exactly as
    it does on a real `evaluate_proofs.py` invocation -- an in-process test
    against an already-imported `budget` module cannot see this bug, which
    is precisely how it shipped once already (`os.environ` held the right
    values; `budget.py`'s already-computed constants did not)."""
    code = (
        "import os, sys; sys.path.insert(0, %r); "
        "os.environ['MRA_PROVER'] = 'math_v2'; "
        "from pipeline.proving import budget_profile; "
        "budget_profile('hard-reasoning'); "
        "from math_v2.core import budget; "
        "assert budget.MAX_LEAN_CALLS == 40, budget.MAX_LEAN_CALLS; "
        "assert budget.MAX_SECONDS == 3600.0, budget.MAX_SECONDS; "
        "print('OK')"
    ) % str(ROOT)

    clean_env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("MRA_MAX_AGENT") and k != "MRA_PROVER"
    }
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=clean_env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
