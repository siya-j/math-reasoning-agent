"""Formal proof tools — Lean 4 with Mathlib.

NO `from __future__ import annotations` (§5.1, gotcha 1).

Thin wrappers. Every docstring below is prompt text the model reads to decide
whether to call the tool (§5.2), and every body delegates to
`math_v2/core/proving.py`, which is tested with no container and no model.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from math_v2.context import MathContext
from math_v2.core import log, proving
from math_v2.tools._util import lean_runner


def _goal(runtime, statement):
    """The statement to work on: the one given, else the one being worked on."""
    workdir = runtime.context.workdir
    if statement.strip():
        log.set_goal(workdir, statement.strip())
        return workdir, statement.strip()
    return workdir, log.current_goal(workdir)


def _no_goal():
    return {
        "ok": False,
        "error": "no_statement",
        "message": (
            "No formal statement is set. Call `check_statement` with the Lean "
            "theorem signature first."
        ),
    }


@tool
async def check_statement(statement: str, runtime: ToolRuntime[MathContext]) -> dict:
    """Check that a Lean theorem signature makes sense before proving it.

    Call this FIRST for any claim you intend to prove. It compiles the
    signature with a placeholder proof, so the only thing under test is whether
    Lean can understand the claim. A signature naming something Mathlib no
    longer has can never be proved by anyone, and finding that out costs one
    compilation instead of eight.

    It also sets the statement for the other proof tools, so you do not need to
    repeat it.

    Args:
        statement: the complete Lean 4 theorem signature, beginning `theorem`
            or `lemma` and ending just before `:=`. Do not include a proof.
    """
    workdir, goal = _goal(runtime, statement)
    if not goal:
        return _no_goal()
    return await proving.check_statement(workdir, goal, lean_runner(workdir))


@tool
async def try_proof(proof: str, runtime: ToolRuntime[MathContext],
                    statement: str = "") -> dict:
    """Compile a candidate proof. This is the ONLY thing that can prove a goal.

    Use it as often as you need — a rejected attempt costs time and nothing
    else, and the goal state it returns is the most useful information
    available. Read that goal state: it says exactly what remains. Change your
    approach in response to it rather than resubmitting a variation.

    Args:
        proof: the proof body only — what follows `:=`. Do not restate the
            theorem; the declaration is assembled for you. Never use `sorry` or
            `admit`: they compile and prove nothing, and are rejected.
        statement: only if proving something other than the current statement.
    """
    workdir, goal = _goal(runtime, statement)
    if not goal:
        return _no_goal()
    return await proving.try_proof(workdir, goal, proof, lean_runner(workdir))


@tool
async def try_standard_tactics(runtime: ToolRuntime[MathContext],
                               statement: str = "") -> dict:
    """Try the usual closers and every premise found so far, in one compilation.

    Runs `norm_num`, `simp`, `decide`, `aesop` and similar, plus `exact` and
    `apply` forms against everything `search_mathlib` has returned. Cheap
    relative to what it covers — about thirty candidates for one compilation —
    so it is worth trying before writing a proof by hand.

    Args:
        statement: only if working on something other than the current
            statement.
    """
    workdir, goal = _goal(runtime, statement)
    if not goal:
        return _no_goal()
    return await proving.try_standard_tactics(workdir, goal, lean_runner(workdir))


@tool
async def try_lemma(statement: str, proof: str,
                    runtime: ToolRuntime[MathContext]) -> dict:
    """Prove a smaller helper result and keep it for the rest of the session.

    A kept lemma can be cited by name in everything you write afterwards, as if
    it were part of Mathlib, and later lemmas may build on earlier ones. Use
    this when the whole proof is too large to write at once: prove the pieces,
    then assemble them.

    Proving a lemma is real progress and is NOT proving the goal — the goal
    still needs `try_proof`.

    Args:
        statement: a complete Lean signature beginning `theorem <name>` or
            `lemma <name>`. Give it a name Mathlib does not already use.
        proof: the proof body of that lemma.
    """
    workdir = runtime.context.workdir
    return await proving.try_lemma(workdir, statement, proof, lean_runner(workdir))


@tool
async def try_skeleton(proof: str, runtime: ToolRuntime[MathContext],
                       statement: str = "") -> dict:
    """Check that a decomposition holds together before filling it in.

    Submit a proof whose steps are stated but not yet proved, each left as
    `sorry`. If it typechecks, the shape of the argument is right and what
    remains is a set of smaller INDEPENDENT goals, which are listed back to
    you. Prove those with `try_lemma`, then submit the assembled proof.

    This is the one place `sorry` belongs. A skeleton proves nothing on its
    own — it establishes that the plan is well formed.

    Args:
        proof: the proof body, using `have <name> : <claim> := by sorry` for
            each step not yet proved.
        statement: only if working on something other than the current
            statement.
    """
    workdir, goal = _goal(runtime, statement)
    if not goal:
        return _no_goal()
    return await proving.try_skeleton(workdir, goal, proof, lean_runner(workdir))
