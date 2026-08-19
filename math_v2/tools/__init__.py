"""Tool catalog and collector.

NO `from __future__ import annotations` (§5.1, gotcha 1).

Two jobs per §5.4: tag the tools with catalog metadata (which drives the UI
tool drawer and the MCP manifest) and expose one collector the factory calls.

DELIBERATELY ABSENT
-------------------
No `math_python_execute`. `chem_v2` has an escape hatch and the blueprint
offers one as a standard ingredient, but for this agent it is different in
kind: arbitrary model-written Python producing a mathematical result is
precisely what the guard exists to prevent. If it is ever added, its results
must be non-authoritative — the same status SymPy has on the proving path.

Not re-implemented, because deepagents supplies them through the backend:
`write_todos`, `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`,
`execute`. Not added, because `NarrationMiddleware` contributes them:
`narrate`, `annotate_artifact`.
"""

from math_v2.tools.control import finish
from math_v2.tools.proving import (
    check_statement,
    proof_state,
    try_lemma,
    try_proof,
    try_refutation,
    try_skeleton,
    try_standard_tactics,
)
from math_v2.tools.retrieval import search_mathlib
from math_v2.tools.symbolic import SYMBOLIC_TOOLS

_SIF = "math.sif"

PROOF_TOOLS = [
    check_statement,
    search_mathlib,
    try_standard_tactics,
    try_proof,
    try_lemma,
    try_skeleton,
    try_refutation,
    proof_state,
]

TAGS = {
    "check_equality": ["algebra", "identity", "derivative", "integral"],
    "check_numeric": ["arithmetic", "evaluate"],
    "check_primality": ["prime", "number-theory"],
    "solve_equation": ["solve", "roots", "equation"],
    "check_limit": ["limit", "calculus"],
    "check_series": ["series", "expansion", "taylor"],
    "check_matrix": ["matrix", "linear-algebra"],
    "check_inequality": ["inequality", "bound"],
    "check_factorization": ["factorisation", "number-theory"],
    "check_statement": ["lean", "formalisation"],
    "search_mathlib": ["mathlib", "lemma", "search"],
    "try_standard_tactics": ["lean", "tactics"],
    "try_proof": ["lean", "proof", "compile"],
    "try_lemma": ["lean", "lemma", "decomposition"],
    "try_skeleton": ["lean", "decomposition", "plan"],
    "try_refutation": ["lean", "counterexample", "negation"],
    "proof_state": ["progress", "review", "replan"],
    "finish": ["report", "verdict"],
}


def _tag():
    """Best effort. Catalog metadata is presentation, and its absence must not
    stop the agent from building — but a silent skip would be worse, so the
    failure is recorded on the module for a test to assert against."""
    try:
        from aura_framework.core.mcp.native_meta import IN_PROCESS, tag_tools
    except Exception as exc:  # noqa: BLE001
        return f"native_meta unavailable: {exc}"

    try:
        tag_tools(SYMBOLIC_TOOLS, category="symbolic", sif=_SIF, runtime="math",
                  tags={t.name: TAGS.get(t.name, []) for t in SYMBOLIC_TOOLS})
        tag_tools([check_statement, try_standard_tactics, try_proof, try_lemma,
                   try_skeleton, try_refutation],
                  category="proving", sif=_SIF, runtime="math",
                  tags={n: TAGS[n] for n in
                        ("check_statement", "try_standard_tactics", "try_proof",
                         "try_lemma", "try_skeleton", "try_refutation")})
        # Reads the record and compiles nothing, so it is in-process like the
        # other tools that touch no compute.
        tag_tools([proof_state], category="proving", sif=IN_PROCESS, runtime=None,
                  tags={"proof_state": TAGS["proof_state"]})
        # Retrieval is HTTP on the orchestrator: no SIF, no runtime.
        tag_tools([search_mathlib], category="retrieval", sif=IN_PROCESS,
                  runtime=None, tags={"search_mathlib": TAGS["search_mathlib"]})
        tag_tools([finish], category="control", sif=IN_PROCESS, runtime=None,
                  tags={"finish": TAGS["finish"]})
    except Exception as exc:  # noqa: BLE001
        return f"tagging failed: {exc}"
    return ""


TAGGING_ERROR = _tag()


def _extras():
    """`research_internet` and the gen-UI tools, when the framework has them."""
    found = []
    try:
        from aura_framework.subagents.research import research_internet

        found.append(research_internet)
    except Exception:  # noqa: BLE001
        pass
    try:
        from aura_framework.core.gen_ui.tools import GEN_UI_TOOLS

        found.extend(GEN_UI_TOOLS)
    except Exception:  # noqa: BLE001
        pass
    return found


def create_math_v2_tools():
    """Every tool this agent has. `finish` is last because it ends the work."""
    return [*SYMBOLIC_TOOLS, *PROOF_TOOLS, *_extras(), finish]


__all__ = ["create_math_v2_tools", "PROOF_TOOLS", "SYMBOLIC_TOOLS", "finish"]
