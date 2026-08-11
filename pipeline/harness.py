"""Which agent harness runs the tool-calling step.

The harness is the ONLY thing this module chooses. It does not decide
verdicts, it does not own control flow, and swapping it must not change
either — `pipeline.py`, `guard.py`, `reflection.py` and `router.py` never
learn which one is in use.

    MRA_HARNESS=langchain    create_agent          (default, the baseline)
    MRA_HARNESS=deepagents   create_deep_agent     (planning, subagents, fs)

A SWITCH, NOT A REPLACEMENT
---------------------------
Both stay runnable so the comparison is possible:

    MRA_HARNESS=langchain  python scripts/evaluate.py
    MRA_HARNESS=deepagents python scripts/evaluate.py

Same model, same 109 cases, one variable. Replacing the old harness outright
would make the question "did this help?" unanswerable — and that question is
the whole reason for adopting a platform deliberately rather than by default.

WHY THE GUARANTEE SURVIVES EITHER WAY
-------------------------------------
Tools write to a VerificationLog as a side effect of being CALLED. Whichever
harness invokes them, the record is produced by execution rather than by the
agent's report of execution. The guard reads that log and never the agent's
prose, so no harness can influence a verdict.
"""

from __future__ import annotations

import config

LANGCHAIN = "langchain"
DEEPAGENTS = "deepagents"


def _langchain_agent(model, tools, system_prompt):
    from langchain.agents import create_agent

    return create_agent(model=model, tools=tools, system_prompt=system_prompt)


# Deep Agents ships a virtual filesystem for coding agents. This agent has
# nine verification tools and no files, so those are surface without purpose —
# and measured cost: restraint on abstract claims fell from 100% to 92% under
# the deepagents harness, producing the run's only soundness failure
# (`abs-continuous-differentiable`). Given more ways to act, a model finds
# something to do on an unanswerable question instead of declining.
FILESYSTEM_TOOLS = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep"}
)


def _exclude_filesystem(model) -> None:
    """Hide the filesystem tools from the model, if the harness allows it.

    Written against the published API. If registration is unavailable or its
    signature differs, the agent is still built — with the filesystem tools
    present, which is the previous behaviour rather than a failure.
    """
    try:
        from deepagents import HarnessProfile, register_harness_profile
    except ImportError:
        return

    name = model if isinstance(model, str) else getattr(model, "model_name", None)
    if not name:
        name = config.MODEL.split(":", 1)[-1]

    try:
        register_harness_profile(
            name, HarnessProfile(excluded_tools=FILESYSTEM_TOOLS)
        )
    except Exception:  # noqa: BLE001 - an optimisation, never a hard failure
        return


def _deep_agent(model, tools, system_prompt):
    """Build a LangChain Deep Agent.

    Same three arguments as `create_agent`, which is what makes the switch
    cheap. What differs is what the harness adds on top: a virtual filesystem
    (`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `delete`)
    and a `task` tool for spawning subagents.

    Task planning (`write_todos`) is opt-in from v0.7 and is deliberately NOT
    enabled here. Planning is the capability this project measured a model
    failing to use — `mean checks per case` fell to 0.94 — so it is added
    later, on purpose, with a number attached.
    """
    try:
        from deepagents import create_deep_agent
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "MRA_HARNESS=deepagents but the package is not installed.\n"
            "  pip install deepagents        # needs Python 3.11+\n"
            f"Original error: {exc}"
        ) from exc

    if not config.DEEPAGENTS_FILESYSTEM:
        _exclude_filesystem(model)

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
    )


_BUILDERS = {LANGCHAIN: _langchain_agent, DEEPAGENTS: _deep_agent}


def build_agent(model, tools, system_prompt):
    """Return something with `.invoke({"messages": [...]})`."""
    builder = _BUILDERS.get(config.HARNESS)
    if builder is None:
        raise RuntimeError(
            f"Unknown MRA_HARNESS {config.HARNESS!r}. "
            f"Choose one of: {', '.join(_BUILDERS)}"
        )
    return builder(model, tools, system_prompt)


def final_text(result) -> str:
    """The assistant's last message, tolerating differences between harnesses.

    LangChain returns {"messages": [...]}; LangGraph-based harnesses may
    nest it or use `content` instead of `text`. Prose is only shown to the
    human — it never reaches the guard — so failing to extract it must not
    fail the run.
    """
    if isinstance(result, str):
        return result

    messages = None
    if isinstance(result, dict):
        messages = result.get("messages") or result.get("output")
    if not messages:
        return ""

    last = messages[-1] if isinstance(messages, list) else messages
    for attribute in ("text", "content"):
        value = getattr(last, attribute, None)
        if isinstance(value, str) and value:
            return value
    if isinstance(last, dict):
        for key in ("text", "content"):
            if isinstance(last.get(key), str):
                return last[key]
    return ""
