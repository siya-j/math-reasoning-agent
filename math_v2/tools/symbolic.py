"""Symbolic computation tools — SymPy, dispatched to the op worker.

NO `from __future__ import annotations` (§5.1, gotcha 1).

DOCSTRING RULE, learned from an eval regression: no concrete worked examples
here. A model once copied `check_equality("...", "diff(x**3, x)", "3*x**2")`
verbatim out of a docstring and ran exactly that check for the question "is
2 + 2 = 5?". Describe the arguments; show syntax with placeholders; never show
a fillable-looking call with real values in it.

Nine tools rather than one generic `compute`, because the narrowness is what
makes them reliable: each docstring tells the model precisely when it applies.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from math_v2.context import MathContext
from math_v2.core import budget, symbolic
from math_v2.tools._enums import RelationLit
from math_v2.tools._util import worker_dispatch

SYNTAX = (
    "SymPy syntax: ** for powers, explicit multiplication (2*x, never 2x), "
    "diff(<expr>, <var>), integrate(<expr>, <var>), sin/cos/log/exp/sqrt/pi."
)


async def _run(runtime, op, **args):
    workdir = runtime.context.workdir
    stop = budget.spend(workdir, symbolic=True)
    if stop:
        return stop
    return await symbolic.compute(workdir, op, args, worker_dispatch(workdir))


@tool
async def check_equality(lhs: str, rhs: str, runtime: ToolRuntime[MathContext],
                         variable: str = "x") -> dict:
    """Check whether two expressions are equal for all values of the variable.

    Use for identities, derivatives and integrals. Both sides must be taken
    from the question as the user stated them.

    Args:
        lhs: the left expression.
        rhs: the right expression.
        variable: the variable they are in.
    """
    return await _run(runtime, "check_equality", lhs=lhs, rhs=rhs, variable=variable)


@tool
async def check_numeric(lhs: str, rhs: str,
                        runtime: ToolRuntime[MathContext]) -> dict:
    """Check whether a numeric expression evaluates to an expected number.

    Use only when both sides are concrete numbers with no free variables.

    Args:
        lhs: the expression to evaluate.
        rhs: the number it is claimed to equal.
    """
    return await _run(runtime, "check_numeric", lhs=lhs, rhs=rhs)


@tool
async def check_primality(n: str, runtime: ToolRuntime[MathContext]) -> dict:
    """Decide whether an integer is prime, with its factorisation if not.

    Args:
        n: the integer, as it appears in the question.
    """
    return await _run(runtime, "check_primality", lhs=n)


@tool
async def solve_equation(lhs: str, rhs: str, candidate: str,
                         runtime: ToolRuntime[MathContext],
                         variable: str = "x") -> dict:
    """Check whether the solutions of an equation are EXACTLY those claimed.

    This checks the whole solution set, so it catches a claim that misses a
    solution as well as one that adds a spurious root.

    Args:
        lhs: the left side of the equation.
        rhs: the right side.
        candidate: the claimed solutions, comma separated.
        variable: the variable to solve for.
    """
    return await _run(runtime, "solve_equation", lhs=lhs, rhs=rhs,
                      candidate=candidate, variable=variable)


@tool
async def check_limit(lhs: str, rhs: str, point: str,
                      runtime: ToolRuntime[MathContext],
                      variable: str = "x") -> dict:
    """Check whether an expression tends to a claimed value at a point.

    Args:
        lhs: the expression.
        rhs: the claimed limit.
        point: what the variable approaches (a number, or oo / -oo).
        variable: the variable that moves.
    """
    return await _run(runtime, "check_limit", lhs=lhs, rhs=rhs, point=point,
                      variable=variable)


@tool
async def check_series(lhs: str, rhs: str, runtime: ToolRuntime[MathContext],
                       variable: str = "x", point: str = "0",
                       order: str = "6") -> dict:
    """Check a series expansion of an expression about a point.

    Args:
        lhs: the expression to expand.
        rhs: the claimed expansion.
        variable: the variable expanded in.
        point: the point expanded about.
        order: how many terms to compare.
    """
    return await _run(runtime, "check_series", lhs=lhs, rhs=rhs, variable=variable,
                      point=point, order=order)


@tool
async def check_matrix(lhs: str, rhs: str,
                       runtime: ToolRuntime[MathContext]) -> dict:
    """Check whether two matrix expressions are equal.

    Args:
        lhs: the left matrix expression, in SymPy Matrix syntax.
        rhs: the right matrix expression.
    """
    return await _run(runtime, "check_matrix", lhs=lhs, rhs=rhs)


@tool
async def check_inequality(lhs: str, rhs: str, relation: RelationLit,
                           runtime: ToolRuntime[MathContext],
                           variable: str = "x") -> dict:
    """Check whether an inequality holds for EVERY real value of the variable.

    A counterexample is reported when one exists, which is usually the more
    useful answer.

    Args:
        lhs: the left expression.
        rhs: the right expression.
        relation: which inequality is claimed.
        variable: the variable it must hold for.
    """
    return await _run(runtime, "check_inequality", lhs=lhs, rhs=rhs,
                      relation=relation, variable=variable)


@tool
async def check_factorization(number: str, factorization: str,
                              runtime: ToolRuntime[MathContext]) -> dict:
    """Check whether a claimed prime factorisation of an integer is correct.

    Args:
        number: the integer.
        factorization: the claimed factorisation, as a product expression.
    """
    return await _run(runtime, "check_factorization", lhs=number, rhs=factorization)


SYMBOLIC_TOOLS = [
    check_equality,
    check_numeric,
    check_primality,
    solve_equation,
    check_limit,
    check_series,
    check_matrix,
    check_inequality,
    check_factorization,
]
