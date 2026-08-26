"""Formal proof tools — Lean 4 with Mathlib.

NO `from __future__ import annotations` (§5.1, gotcha 1).

Thin wrappers. Every docstring below is prompt text the model reads to decide
whether to call the tool (§5.2), and every body delegates to
`math_v2/core/proving.py`, which is tested with no container and no model.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from math_v2.context import MathContext
from math_v2.core import budget, log, progress, proving
from math_v2.tools._util import lean_runner


def _goal(runtime, statement):
    """The statement to work on: the one given, else the declared goal.

    NOT `current_goal` for the fallback. Two MEASURED failures, closed by the
    same one-line change: (1) `check_statement("")` used to fall back to
    `current_goal`, which a prior diversion (any tool called with an explicit
    `statement`) may have left pointed at something other than the real goal
    -- so an empty-argument statement CHECK could silently re-declare a stale
    diversion as the goal. (2) after a diversion, a later no-argument call to
    `try_proof`/`try_standard_tactics`/`try_skeleton` -- intended to resume
    the real goal -- would silently keep compiling against the diversion
    instead, burning budget on the wrong statement with no error to notice.
    Falling back to the declared goal instead means "no statement given"
    always resumes what was actually declared, never whatever was last
    touched.
    """
    workdir = runtime.context.workdir
    if statement.strip():
        log.set_goal(workdir, statement.strip())
        return workdir, statement.strip()
    return workdir, log.declared_goal(workdir)


def _charge(runtime, **kind):
    """Budget check for a tool that is about to do expensive work.

    Returns a structured stop, or None to proceed. Enforced HERE, in the tool
    layer, so no compilation, dispatch or lookup happens once a limit is hit —
    independently of any middleware and of what the model decides to do next.

    `goal_state=True` on the four tools that return one. `check_statement` does
    not: it reports elaborates / does not, which tells you nothing about which
    lemma to search for, and treating it as feedback let each statement check
    buy three more searches. See the note in core/budget.py.
    """
    return budget.spend(runtime.context.workdir, **kind)


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
async def proof_state(runtime: ToolRuntime[MathContext]) -> dict:
    """Review what you have established so far, before deciding what to do next.

    Costs nothing — it reads the record of what already ran and compiles
    nothing. Call it when a proof has been rejected more than once, when you
    are about to change approach, or when you have proved helper lemmas and
    want to assemble them.

    It reports the goal, the auxiliary lemmas you have proved and can cite by
    name, the steps still left as `sorry` in your last working decomposition,
    the attempts the compiler rejected and why, any symbolic results, and what
    the budget has spent.
    """
    workdir = runtime.context.workdir
    state = progress.snapshot(workdir)
    return {"ok": True, "outputs": state, "message": progress.render(state)}


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
    stop = _charge(runtime, lean=True, statement_check=True)
    if stop:
        return stop
    workdir, goal = _goal(runtime, statement)
    if not goal:
        return _no_goal()
    # No separate "declare the goal" write happens here. `proving.check_statement`
    # below always appends a STATEMENT_CHECK record carrying this exact `goal`,
    # and `log.declared_goal` reads the last one of those — so this is the ONLY
    # tool whose calls become what the run is reported and scored against, with
    # no second field to keep in sync. `try_proof`/`try_standard_tactics`/
    # `try_skeleton` still update `current_goal` (via `_goal()`) so a diversion
    # compiles and keeps working exactly as before; none of them ever appends a
    # STATEMENT_CHECK record, so a one-off diversion to an auxiliary claim never
    # becomes what the run is reported as being about.
    from math_v2.tools.retrieval import get_search

    return await proving.check_statement(
        workdir, goal, lean_runner(workdir), get_search()
    )


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
    stop = _charge(runtime, lean=True, goal_state=True)
    if stop:
        return stop
    workdir, goal = _goal(runtime, statement)
    if not goal:
        return _no_goal()
    from math_v2.tools.retrieval import get_search

    result = await proving.try_proof(workdir, goal, proof, lean_runner(workdir),
                                     get_search())
    # An automatic `exact` repair compiles a second time inside this one call.
    budget.charge_lean(workdir, result.get("outputs", {}).get("compiles_used", 0))
    return result


@tool
async def try_refutation(proof: str, runtime: ToolRuntime[MathContext],
                         statement: str = "") -> dict:
    """Prove that the goal is FALSE as written, when you believe it is.

    Use this when a rejected attempt has shown you the statement is missing a
    hypothesis — a ProofNet row that says `IsOpen` where the textbook says
    connected, say. Arguing in prose that a statement is broken does not
    establish it; a compiled proof of the negation does, and it is a real
    result rather than a failure to prove.

    Args:
        proof: the proof body only, what follows `:=`. Give the CONCRETE
            counterexample — the actual function, set and points — then derive
            the contradiction. `sorry` and `admit` are refused.
        statement: leave empty and the negation of the current goal is built
            for you, binders and all. Pass one only to refute something else;
            it must conclude a negation or it is refused.
    """
    stop = _charge(runtime, lean=True, goal_state=True)
    if stop:
        return stop
    workdir = runtime.context.workdir
    if not statement.strip() and not log.current_goal(workdir):
        return _no_goal()
    return await proving.try_refutation(
        workdir, statement.strip(), proof, lean_runner(workdir)
    )


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
    stop = _charge(runtime, lean=True, goal_state=True)
    if stop:
        return stop
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
    stop = _charge(runtime, lean=True, goal_state=True)
    if stop:
        return stop
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
    stop = _charge(runtime, lean=True, goal_state=True)
    if stop:
        return stop
    workdir, goal = _goal(runtime, statement)
    if not goal:
        return _no_goal()
    # The skeleton call itself is already charged above. What is left, minus a
    # margin so the model still has compiles for the assembled proof, is what
    # automatic hole-filling may spend.
    fill_budget = max(0, min(proving.MAX_AUTO_FILLS + 1,
                             budget.lean_remaining(workdir) - 2))
    result = await proving.try_skeleton(workdir, goal, proof,
                                        lean_runner(workdir), fill_budget)
    budget.charge_lean(workdir, result.get("outputs", {}).get("compiles_used", 0))
    return result
