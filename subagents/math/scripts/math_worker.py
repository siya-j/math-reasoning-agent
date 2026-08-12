"""SymPy op registry — one process, many small operations.

    echo '{"lhs": "diff(x**3, x)", "rhs": "3*x**2"}' | python3 -m math_worker check_equality

Blueprint AGENT_BLUEPRINT.md §5.3, the `builder_v2` shape: dozens of small ops
sharing one heavy import. SymPy costs about a second to import, and a process
per tool call would be dominated by it. One worker module amortises that across
every op, and adding an operation later is a registry entry rather than a new
dispatch path.

WHAT THIS IS NOT
----------------
No decisions are made here. Every op delegates to `verifiers/sympy_verifier.py`,
which is unchanged and still carries its own tests. This module is a transport:
JSON in, JSON out, one process boundary. That is deliberate — the migration
brief is to move tested logic, not to rewrite it.

THE OUTPUT ENVELOPE
-------------------
    {"ok": true,  "outputs": {...}}
    {"ok": false, "error": "..."}

`ok` describes whether the OPERATION RAN, never whether the claim is true. A
claim decided FALSE is a successful run: `{"ok": true, "outputs":
{"status": "false", ...}}`. Conflating the two would make a refuted claim look
like a broken tool, and the caller would retry it forever.

NOTHING HERE RAISES past `main`. A bad expression is a result, not a crash.
"""

import json
import math
import sys

_HERE = __file__


def _project_root() -> str:
    """Locate the package root, in the SIF or in a local checkout.

    Baked into the image at /app/... and put on PYTHONPATH (blueprint §7.3),
    but a local run has it four directories up. Both are supported so the same
    file is testable without building a 3 GB container.
    """
    import os

    return os.path.abspath(os.path.join(os.path.dirname(_HERE), "..", "..", ".."))


if _project_root() not in sys.path:
    sys.path.insert(0, _project_root())

from domain.verdict import VerificationStatus                      # noqa: E402
from domain.verification import VerificationKind, VerificationRequest  # noqa: E402
from verifiers.sympy_verifier import SymPyVerifier                 # noqa: E402

_VERIFIER = SymPyVerifier()

# op name -> the kind it builds, and the argument names it accepts.
# The names match VerificationRequest's fields exactly, so the tool layer and
# the worker cannot drift apart.
OPS: dict[str, tuple[VerificationKind, tuple[str, ...]]] = {
    "check_equality": (VerificationKind.EQUALITY, ("lhs", "rhs", "variable")),
    "check_numeric": (VerificationKind.NUMERIC, ("lhs", "rhs")),
    "check_primality": (VerificationKind.PRIMALITY, ("lhs",)),
    "solve_equation": (
        VerificationKind.SOLUTION, ("lhs", "rhs", "variable", "candidate"),
    ),
    "check_limit": (VerificationKind.LIMIT, ("lhs", "rhs", "variable", "point")),
    "check_series": (
        VerificationKind.SERIES, ("lhs", "rhs", "variable", "point", "order"),
    ),
    "check_matrix": (VerificationKind.MATRIX, ("lhs", "rhs")),
    "check_inequality": (
        VerificationKind.INEQUALITY, ("lhs", "rhs", "variable", "relation"),
    ),
    "check_factorization": (VerificationKind.FACTORIZATION, ("lhs", "rhs")),
}


def json_safe(value):
    """Replace NaN and Inf with None, everywhere, at any depth.

    Blueprint gotcha 9: a raw NaN in a tool's dict makes the model API reject
    the entire request — not the tool call, the whole request. SymPy produces
    infinities routinely (a divergent limit is a correct answer), so this is
    on the likely path rather than a defensive flourish.
    """
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def run_op(op: str, args: dict) -> dict:
    """Execute one operation and return the envelope. Never raises."""
    if op not in OPS:
        return {
            "ok": False,
            "error": f"Unknown op {op!r}. Known: {', '.join(sorted(OPS))}.",
        }

    kind, accepted = OPS[op]
    unexpected = set(args) - set(accepted)
    if unexpected:
        # Loud rather than ignored: a silently dropped argument means the
        # verifier checked something other than what was asked, which is
        # failure 3 in this project's log.
        return {
            "ok": False,
            "error": (
                f"{op} does not accept {', '.join(sorted(unexpected))}. "
                f"Accepted: {', '.join(accepted)}."
            ),
        }

    fields = {name: str(args[name]) for name in accepted if args.get(name) is not None}

    try:
        verdict = _VERIFIER.verify(VerificationRequest(kind=kind, **fields))
    except Exception as exc:  # noqa: BLE001 - a bad expression is a result
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "outputs": json_safe(
            {
                "status": verdict.status.value,
                "method": verdict.method,
                "detail": verdict.detail,
                # Convenience for the tool layer's own guard: it must not have
                # to parse prose to learn whether anything was decided.
                "decided": verdict.status
                in (VerificationStatus.TRUE, VerificationStatus.FALSE),
            }
        ),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: math_worker <op>"}))
        return 2

    raw = sys.stdin.read().strip()
    try:
        args = json.loads(raw) if raw else {}
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": f"arguments were not JSON: {exc}"}))
        return 1

    if not isinstance(args, dict):
        print(json.dumps({"ok": False, "error": "arguments must be a JSON object"}))
        return 1

    result = run_op(argv[1], args)
    print(json.dumps(result))
    # Exit 0 whenever the op RAN. A refuted claim is a successful execution,
    # and a non-zero code would make the dispatcher treat it as a failure.
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
