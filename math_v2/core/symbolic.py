"""Symbolic computation tool bodies — thin wrappers over the op worker.

NO `from __future__ import annotations` (blueprint §5.1).

Every function here builds an argument dict and hands it to an injected async
`dispatch(op, args) -> envelope`. In Aura that dispatch is a CommandSpec
running `python3 -m math_worker <op>` inside math.sif; in tests it is a fake,
or the worker called directly.

THE RULE THAT LIMITS ALL OF THIS
--------------------------------
SymPy DECIDES computations — it may return TRUE or FALSE and that is a real
verdict. It may NEVER establish a PROOF. On the proving path a symbolic result
informs the model and stops there; `verdict.refuse` enforces it, and `finish`
will not accept a computation offered as a proof.

Highest-value use is computing a WITNESS for an existential goal: a CAS
produces the actual number instantly, where Lean checks proofs but does not
search for them.
"""

from math_v2.core import log

# op -> the arguments it takes. Mirrors math_worker.OPS so the two cannot
# drift; there is a test asserting they agree.
OPS = {
    "check_equality": ("lhs", "rhs", "variable"),
    "check_numeric": ("lhs", "rhs"),
    "check_primality": ("lhs",),
    "solve_equation": ("lhs", "rhs", "variable", "candidate"),
    "check_limit": ("lhs", "rhs", "variable", "point"),
    "check_series": ("lhs", "rhs", "variable", "point", "order"),
    "check_matrix": ("lhs", "rhs"),
    "check_inequality": ("lhs", "rhs", "variable", "relation"),
    "check_factorization": ("lhs", "rhs"),
}


async def compute(workdir, op, args, dispatch):
    """Run one symbolic operation and record that it happened.

    Returns the worker's envelope with a `message` added for the model. The
    envelope's `ok` means the operation RAN; `outputs.status` is the verdict.
    A refuted claim is a successful run, and conflating the two would make the
    agent retry a correct refutation until its budget expired.
    """
    if op not in OPS:
        return {
            "ok": False,
            "error": "Unknown operation {!r}. Known: {}.".format(
                op, ", ".join(sorted(OPS))
            ),
        }

    payload = {k: v for k, v in args.items() if v is not None and k in OPS[op]}
    envelope = await dispatch(op, payload)

    if not envelope.get("ok"):
        return {
            "ok": False,
            "error": envelope.get("error", "the computation could not be run"),
            "message": (
                "The computation could not be run: "
                f"{envelope.get('error', 'unknown error')}"
            ),
        }

    outputs = envelope.get("outputs", {})
    log.append(workdir, log.Record(
        kind="computation",
        statement="{}({})".format(op, ", ".join(f"{k}={v!r}" for k, v in payload.items())),
        status=outputs.get("status", log.UNKNOWN),
        detail=outputs.get("detail", ""),
    ))

    return {
        "ok": True,
        "outputs": outputs,
        "message": _message(outputs),
    }


def _message(outputs):
    status = outputs.get("status")
    detail = outputs.get("detail", "")

    if status == log.TRUE:
        return f"TRUE. {detail}"
    if status == log.FALSE:
        # Worth stating plainly: a refutation is a real, useful answer, and
        # the model should not treat it as a tool failure to be retried.
        return (
            f"FALSE. {detail}\nThis is a definite answer, not a failure. If you "
            "were about to prove this claim, stop — it does not hold."
        )
    return (
        f"UNDECIDED. {detail}\nThe computation ran and settled nothing, which "
        "is not evidence either way."
    )
