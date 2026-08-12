"""Tests for the SymPy op worker. No SIF, no dispatch — the module directly.

`test_a_refuted_claim_is_a_SUCCESSFUL_run` is the load-bearing one. `ok`
describes whether the operation RAN. If a FALSE verdict were reported as
`ok: false`, the dispatcher would treat a correctly refuted claim as a broken
tool and the agent would retry it until the budget ran out.
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "subagents" / "math" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import math_worker  # noqa: E402
from domain.verification import VerificationRequest  # noqa: E402
from verifiers.sympy_verifier import SymPyVerifier  # noqa: E402


# --------------------------------------------------------------- the envelope
def test_a_refuted_claim_is_a_SUCCESSFUL_run():
    """FALSE is an answer. Only a failure to RUN is `ok: false`."""
    result = math_worker.run_op("check_primality", {"lhs": "561"})

    assert result["ok"] is True, "a refutation was reported as a broken tool"
    assert result["outputs"]["status"] == "false"
    assert result["outputs"]["decided"] is True


def test_a_verdict_of_unknown_is_also_a_successful_run():
    result = math_worker.run_op(
        "check_equality", {"lhs": "sin(x)**2 + cos(x)**2", "rhs": "2"}
    )
    assert result["ok"] is True
    assert result["outputs"]["decided"] is False


def test_decided_is_computed_here_so_the_tool_layer_need_not_read_prose():
    for expression, decided in [("7919", True), ("x", False)]:
        result = math_worker.run_op("check_primality", {"lhs": expression})
        assert result["outputs"]["decided"] is decided


# ------------------------------------------------------------------ the ops
def test_every_registered_op_is_supported_by_the_verifier():
    """A registry entry the verifier cannot handle would fail only at runtime."""
    verifier = SymPyVerifier()
    for op, (kind, _fields) in math_worker.OPS.items():
        assert verifier.supports(VerificationRequest(kind=kind)), op


def test_the_nine_tools_all_have_an_op():
    assert set(math_worker.OPS) == {
        "check_equality",
        "check_numeric",
        "check_primality",
        "solve_equation",
        "check_limit",
        "check_series",
        "check_matrix",
        "check_inequality",
        "check_factorization",
    }


def test_op_argument_names_match_the_request_fields():
    """Otherwise the tool layer and the worker drift apart silently."""
    fields = set(VerificationRequest(kind=next(iter(math_worker.OPS.values()))[0]).__dict__)
    for op, (_kind, accepted) in math_worker.OPS.items():
        assert set(accepted) <= fields, op


# --------------------------------------------------------------- bad input
def test_an_unknown_op_names_the_ones_that_exist():
    result = math_worker.run_op("check_vibes", {})
    assert result["ok"] is False
    assert "check_equality" in result["error"]


def test_an_unexpected_argument_is_refused_rather_than_dropped():
    """A silently ignored argument means something else was checked.

    That is failure 3 in this project's log: "is 2 the only solution?" checked
    as "are the solutions 2 and -2?".
    """
    result = math_worker.run_op("check_primality", {"lhs": "7", "rhs": "nonsense"})
    assert result["ok"] is False
    assert "rhs" in result["error"]


def test_an_unparseable_expression_is_a_result_and_not_a_crash():
    result = math_worker.run_op("check_equality", {"lhs": "))(", "rhs": "1"})
    assert result["ok"] is True
    assert result["outputs"]["decided"] is False


def test_the_parser_still_refuses_python_builtins():
    """Regression guard: the worker must not widen the allow-list."""
    result = math_worker.run_op(
        "check_equality", {"lhs": '__import__("os").system("echo pwned")', "rhs": "1"}
    )
    assert result["outputs"]["status"] != "true"


# ------------------------------------------------------------ gotcha 9: NaN
def test_nan_and_inf_never_reach_the_output():
    """A raw NaN makes the model API reject the whole request, not just the call."""
    cleaned = math_worker.json_safe(
        {"a": float("nan"), "b": [float("inf"), 1.0], "c": {"d": float("-inf")}}
    )
    assert cleaned == {"a": None, "b": [None, 1.0], "c": {"d": None}}
    assert json.dumps(cleaned)          # serialises without NaN literals


def test_json_safe_leaves_ordinary_values_alone():
    payload = {"s": "text", "i": 3, "f": 1.5, "b": True, "n": None}
    assert math_worker.json_safe(payload) == payload


# ---------------------------------------------------------------- the CLI
def run_cli(op: str, args: dict):
    completed = subprocess.run(
        [sys.executable, "-m", "math_worker", op],
        input=json.dumps(args),
        capture_output=True,
        text=True,
        cwd=SCRIPTS,
    )
    return completed, json.loads(completed.stdout)


def test_the_cli_round_trips_through_stdin_and_stdout():
    completed, payload = run_cli("check_primality", {"lhs": "7919"})
    assert completed.returncode == 0
    assert payload["outputs"]["status"] == "true"


def test_the_cli_exits_zero_for_a_refuted_claim():
    """It ran. A non-zero code would make the dispatcher call it a failure."""
    completed, payload = run_cli("check_primality", {"lhs": "561"})
    assert completed.returncode == 0
    assert payload["outputs"]["status"] == "false"


def test_the_cli_exits_non_zero_when_the_op_could_not_run():
    completed, payload = run_cli("check_vibes", {})
    assert completed.returncode == 1
    assert payload["ok"] is False


def test_the_cli_reports_malformed_json_instead_of_crashing():
    completed = subprocess.run(
        [sys.executable, "-m", "math_worker", "check_primality"],
        input="{not json",
        capture_output=True,
        text=True,
        cwd=SCRIPTS,
    )
    assert json.loads(completed.stdout)["ok"] is False


def test_the_cli_prints_only_json():
    """The dispatcher parses stdout. Anything else on it breaks the contract."""
    completed, _ = run_cli("check_equality", {"lhs": "diff(x**3, x)", "rhs": "3*x**2"})
    assert completed.stdout.count("\n") == 1
