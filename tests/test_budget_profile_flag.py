"""`--budget-profile hard-reasoning` — a real step up, not SOTA's scale.

WHY THIS EXISTS
---------------
`math_v2/core/budget.py`'s constants are module-level globals read ONCE via
`os.getenv` at first import — there is no function anywhere that re-reads
them per goal or per tier. `BENCHMARK_2026_08` already sets the precedent for
a NAMED profile that is nonetheless "applied by setting the environment,
never by changing the defaults" — a human copies its values into a shell.
`HARD_REASONING` is the same shape of dict, but `--budget-profile` makes it
actually applicable from the CLI, via `os.environ.setdefault` BEFORE the
first `prove()` call (which is what first imports `math_v2.core.budget` and
triggers its module-level reads) — confirmed by tracing
`scripts/evaluate_proofs.py`'s own import graph, not assumed.

These tests target `apply_budget_profile`'s `os.environ.setdefault` logic
directly, never `main()` (which needs a model and Lean to reach the goal
loop) — the same reason existing budget tests monkeypatch the already-
imported `budget` module directly rather than re-importing it to see a
fresh `os.getenv` read.

ROUTED THROUGH `pipeline.proving.budget_profile`, NOT A DIRECT `math_v2`
IMPORT — the same seam `environment()`/`prove()` already use, so
`scripts/evaluate_proofs.py` stays prover-agnostic
(`tests/test_mathv2_integration.py::test_evaluate_proofs_never_imports_a_prover_directly`
forbids the literal substring `math_v2` there). One real consequence: the
profile only applies when `config.PROVER == "math_v2"` — its env var names
(`MRA_MAX_AGENT_LEAN` etc.) are math_v2's own and have no effect on any other
configured prover, so `budget_profile` returns `{}` and touches nothing on
purpose in that case, rather than silently setting variables nobody reads.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402
from evaluate_proofs import apply_budget_profile  # noqa: E402
from math_v2.core import budget  # noqa: E402


def test_hard_reasoning_profile_values_are_present():
    assert budget.HARD_REASONING == {
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
    assert int(budget.HARD_REASONING["MRA_MAX_AGENT_LEAN"]) > 12


def test_applying_the_profile_sets_environment_defaults(monkeypatch):
    monkeypatch.setattr(config, "PROVER", "math_v2")
    for key in budget.HARD_REASONING:
        monkeypatch.delenv(key, raising=False)

    applied = apply_budget_profile("hard-reasoning")

    assert applied == budget.HARD_REASONING
    for key, value in budget.HARD_REASONING.items():
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
    for key in budget.HARD_REASONING:
        monkeypatch.delenv(key, raising=False)

    applied = apply_budget_profile("hard-reasoning")

    assert applied == {}
    for key in budget.HARD_REASONING:
        assert key not in os.environ


def test_the_budget_profile_flag_is_wired_into_the_parser():
    source = Path("scripts/evaluate_proofs.py").read_text(encoding="utf-8")

    assert '"--budget-profile"' in source
    assert 'choices=("hard-reasoning",)' in source
