"""The package against AGENT_BLUEPRINT.md — layout, gotchas, and the tools.

These are the checks the blueprint's own §9 verification cannot run outside the
Aura tree. They catch the failures it calls the most common: gotcha 1
(stringified annotations silently breaking ToolRuntime injection) and gotcha 8
(re-implementing built-ins).
"""

import asyncio
import inspect
import pathlib
import re

import pytest

from math_v2.tools import PROOF_TOOLS, SYMBOLIC_TOOLS, create_math_v2_tools, finish

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "math_v2"
FUTURE = re.compile(r"^\s*from __future__ import\b", re.MULTILINE)

# §5.1: permitted in these three only. Everywhere else it breaks injection.
MAY_STRINGIFY = {"agent.py", "context.py", "prompt.py", "__init__.py"}


# ------------------------------------------------------------------ gotcha 1
def test_no_tool_module_stringifies_its_annotations():
    """THE most common failure in the repo, and it fails silently."""
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if path.name not in MAY_STRINGIFY and FUTURE.search(path.read_text("utf-8"))
    ]
    assert offenders == [], f"ToolRuntime injection will break in: {offenders}"


def test_the_files_allowed_to_use_it_are_the_ones_the_blueprint_names():
    assert MAY_STRINGIFY == {"agent.py", "context.py", "prompt.py", "__init__.py"}


# ------------------------------------------------------------------- layout
@pytest.mark.parametrize("relative", [
    "__init__.py", "agent.py", "context.py", "prompt.py",
    "tools/__init__.py", "tools/_enums.py", "tools/_util.py", "tools/control.py",
    "tools/proving.py", "tools/symbolic.py", "tools/retrieval.py",
])
def test_the_canonical_files_exist(relative):
    assert (PACKAGE / relative).is_file(), f"missing {relative} (blueprint §2)"


def test_the_context_field_is_named_workdir():
    """Gotcha 6: any other name breaks AuraContext unification for the tree."""
    import dataclasses

    from math_v2.context import MathContext

    assert [f.name for f in dataclasses.fields(MathContext)] == ["workdir"]


def test_the_factory_signature_matches_the_supervisors_call():
    """Gotcha 5: a renamed parameter is a TypeError at supervisor build."""
    source = (PACKAGE / "agent.py").read_text("utf-8")
    for parameter in ("model", "workspace_path", "checkpointer", "context_schema",
                      "skills_middleware", "extra_tools"):
        assert re.search(rf"^\s+{parameter}[:=]", source, re.MULTILINE), parameter
    assert "def create_math_v2_agent(\n    *,\n" in source, "must be keyword-only"
    assert 'name="math"' in source, "the routing id must match the registration"


# -------------------------------------------------------------------- tools
def test_every_tool_is_registered_once():
    tools = create_math_v2_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "a tool is registered twice"
    assert len(tools) == 16


def test_finish_is_present_because_it_is_the_guard():
    """§5.5 calls it optional. For this agent it is not."""
    assert finish in create_math_v2_tools()


def test_the_nine_symbolic_and_five_proving_tools_are_all_there():
    assert len(SYMBOLIC_TOOLS) == 9
    assert {t.name for t in PROOF_TOOLS} == {
        "check_statement", "search_mathlib", "try_standard_tactics",
        "try_proof", "try_lemma", "try_skeleton",
    }


def test_no_builtin_is_reimplemented():
    """Gotcha 8: deepagents supplies these through the backend."""
    supplied = {"write_todos", "read_file", "write_file", "edit_file", "ls",
                "glob", "grep", "execute", "narrate", "annotate_artifact"}
    assert supplied.isdisjoint({t.name for t in create_math_v2_tools()})


def test_the_python_escape_hatch_is_absent():
    """Deliberate: model-written code deciding mathematics defeats the guard."""
    names = {t.name for t in create_math_v2_tools()}
    assert not any("execute" in n or "python" in n for n in names)


def test_every_tool_takes_the_runtime_so_it_can_reach_the_workspace():
    for tool in create_math_v2_tools():
        target = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
        assert "runtime" in inspect.signature(target).parameters, tool.name


def test_every_tool_has_a_docstring_the_model_can_route_on():
    """§5.2: docstrings ARE the routing logic, not developer notes."""
    for tool in create_math_v2_tools():
        assert len(tool.description or "") > 120, f"{tool.name} is underdocumented"


def test_no_symbolic_docstring_contains_a_fillable_example():
    """A model once copied a docstring's worked example verbatim into an
    unrelated question. Placeholders yes, real values no."""
    for tool in SYMBOLIC_TOOLS:
        assert "diff(x**3" not in (tool.description or ""), tool.name


# ------------------------------------------------------------------ imports
def test_the_package_imports_without_the_aura_framework():
    """The tool bodies and the guard must stay testable outside the tree."""
    import math_v2

    assert math_v2.__all__ == ["create_math_v2_agent", "FACTORY_DESCRIPTION"]


def test_the_routing_description_states_a_boundary():
    """§8: without one the supervisor merges this agent with a neighbour."""
    source = (PACKAGE / "agent.py").read_text("utf-8")
    assert "Not for numerical simulation" in source


# ------------------------------------------------------------- the tools run
def runtime_for(workdir):
    from langchain.tools import ToolRuntime

    from math_v2.context import MathContext

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


def test_a_proof_tool_called_before_a_statement_is_set_says_so(tmp_path):
    from math_v2.tools.proving import try_proof

    result = asyncio.run(try_proof.ainvoke(
        {"proof": "trivial", "runtime": runtime_for(tmp_path)}
    ))
    assert result["error"] == "no_statement"


def test_search_degrades_to_a_message_rather_than_an_exception(tmp_path, monkeypatch):
    from math_v2.tools import retrieval

    monkeypatch.setattr(retrieval, "get_search", lambda: None)
    result = asyncio.run(retrieval.search_mathlib.ainvoke(
        {"query": "anything", "runtime": runtime_for(tmp_path)}
    ))
    assert result["ok"] is True
