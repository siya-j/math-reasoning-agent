# Agent Blueprint — how every Aura agent is built

**Audience:** anyone (human or Claude) about to build a new agent in this repo.

**What this is.** Every production agent in Aura is the *same object* — a `create_deep_agent()`
call with four ingredients (prompt, tools, context, SIF runtime) and one shared middleware stack.
This document describes that common core, the execution plane underneath it (Apptainer/SIF), and
the exact touchpoints you must register a new agent in. Then §10 gives a one-paragraph profile of
each existing agent so you can find the one closest to yours and copy *that* instead of starting
from a blank file.

**How to use it (read this first if you are Claude):**

1. Read §1–§8 once — that is the invariant skeleton. Do not invent a different shape.
2. Go to **§10 Agent profiles** and **§11 Which agent is closest to mine?**. Pick the nearest
   existing agent by *shape*, not by scientific domain.
3. Open that agent's real files in the repo (each profile lists them) and follow them line by line.
4. Work through the **§8 wiring checklist** — a missing touchpoint means the agent silently never
   routes, or the graph fails to compile.
5. Run **§9 verification** before claiming done.

> Naming: the `*_v2` packages are the live lean/deepagents agents. The sibling packages without
> `_v2` (`chem/`, `gromacs/`, `qe/`, `hermes/`, …) are the **legacy v1 LangGraph subgraphs** —
> mostly dead weight kept for their scripts, schemas and RAG assets. **Never copy a v1 package as
> the model for a new agent.** Exception: v1 `scripts/` directories are still the compute layer for
> some agents (MACH, builder) — see §5.3.

---

## 1. The 60-second mental model

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ SUPERVISOR  "aura"  (core/session_factory.py)                               │
│   create_deep_agent(subagents=[CompiledSubAgent(...), ...])                  │
│   runtime = "aura"  → aura-base.sif   (no science binaries)                  │
│   delegates with the deepagents `task` tool, gated by AutoDelegateMiddleware  │
└───────────────┬──────────────────────────────────────────────────────────────┘
                │  one AuraContext flows into the whole tree
      ┌─────────┴──────────┬─────────────────────┬──────────────────────┐
      ▼                    ▼                     ▼                      ▼
┌───────────┐        ┌───────────┐         ┌───────────┐         ┌────────────┐
│ chem      │        │ gromacs   │   ...   │ mach      │         │ iris-BOSS  │
│ chem.sif  │        │gromacs.sif│         │ mach.sif  │         │ iris.sif   │
└─────┬─────┘        └─────┬─────┘         └─────┬─────┘         └─────┬──────┘
      │ typed @tool calls build a CommandSpec                          │ delegates again
      ▼                                                               ▼ (analyst subagents)
┌──────────────────────────────────────────────────────────────────────────────┐
│ EXECUTION PLANE                                                              │
│  CommandSpec(runtime="chem", argv=[...], workdir=/workspaces/user/thread)     │
│    → get_backend_for_runtime("chem")                                          │
│      → RabbitMQBackend  ── compute.jobs queue ──►  compute-worker             │
│          → ApptainerBackend: apptainer exec --containall chem.sif <argv>       │
│              /workspace  = the thread workspace (only writable mount)         │
└──────────────────────────────────────────────────────────────────────────────┘
```

Four things define an agent. Everything else is shared:

| Ingredient | Where it lives | What it decides |
|---|---|---|
| **System prompt** | `<agent>_v2/prompt.py` | the persona, workflow order, scope boundaries |
| **Tools** | `<agent>_v2/tools/` | what the agent can actually do |
| **Context** | `<agent>_v2/context.py` | `workdir` injection into tools |
| **Runtime / SIF** | `containers/sif/<name>.def` + `sif_registry.py` | which binaries and libraries exist |

---

## 2. Anatomy — the file layout

The canonical lean agent (copy this shape exactly):

```
aura_framework/subagents/<name>_v2/
├── __init__.py          # re-export create_<name>_v2_agent
├── agent.py             # the factory — ~40 lines, almost entirely boilerplate
├── context.py           # a dataclass with `workdir: str`
├── prompt.py            # <NAME>_SYSTEM_PROMPT (+ COMPUTE_ENV_GUIDANCE)
└── tools/
    ├── __init__.py      # catalog tagging + create_<name>_v2_tools() collector
    ├── _enums.py        # Literal types for tool parameters
    ├── _util.py         # CommandSpec builders / dispatch helpers / parsers
    ├── control.py       # finish (optional — see §5.5)
    ├── dynamic.py       # <name>_python_execute escape hatch (optional)
    └── <domain>.py      # the actual typed tools, grouped by category
```

Sizes for calibration: `agent.py` is 35–85 lines in every agent. `prompt.py` is 100–290 lines.
Tool count ranges from 2 (hermes) to 87 (builder).

---

## 3. The factory — canonical skeleton

Every agent factory has the **same signature**. The SessionFactory calls all of them
identically, so deviating breaks supervisor wiring.

```python
"""<Name> v2 agent factory — creates a Deep Agent for <domain>."""
from __future__ import annotations

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from ..lean_common import make_agent_middleware, make_lean_backend
from .context import MyContext
from .prompt import MY_SYSTEM_PROMPT
from .tools import create_my_v2_tools


def create_my_v2_agent(
    *,
    model: str | BaseChatModel = "google_genai:gemini-3-flash-preview",
    workspace_path: str,
    checkpointer=None,
    context_schema=None,      # SessionFactory passes AuraContext to unify the tree
    skills_middleware=None,   # per-user Studio skills, injected at build time
    extra_tools: list | None = None,   # per-user toolkit (MCP) tools
):
    tools = create_my_v2_tools() + list(extra_tools or [])
    backend = make_lean_backend(workspace_path=workspace_path, runtime="my")
    if checkpointer is None:
        checkpointer = MemorySaver()

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=MY_SYSTEM_PROMPT,
        backend=backend,
        checkpointer=checkpointer,
        context_schema=context_schema or MyContext,
        middleware=make_agent_middleware(
            model, backend, self_validate=True, skills_middleware=skills_middleware
        ),
        name="my",
    )
```

Reference implementations, shortest first:
`qchem_v2/agent.py` (35 lines) · `qe_v2/agent.py` · `gromacs_v2/agent.py` · `chem_v2/agent.py`.

Rules that are load-bearing:

- **Keyword-only args, exact names.** `SessionFactory._build_supervisor` calls
  `factory(model=, workspace_path=, checkpointer=, context_schema=, skills_middleware=, extra_tools=)`
  (`core/session_factory.py:500`). A renamed or positional parameter is a `TypeError` at build time.
- **`name=` is the routing id.** It must equal the key you register in `_V2_FACTORIES`, the
  `RuntimeName` you use (usually), and the frontend `AGENT_META` key.
- **`self_validate=True` for spokes, `False` for anything that supervises.** A supervisor
  legitimately ends turns with no tool calls; `SpokeSelfValidationMiddleware` would loop it back.
  `iris_v2` (a sub-supervisor) omits it; every leaf spoke sets it.
- **`checkpointer` defaults to `MemorySaver()`.** The supervisor owns the durable Postgres
  checkpointer; subagent turns are transient.
- **The graph is workspace-bound.** `make_lean_backend(workspace_path=...)` bakes the workspace in
  at build time, so a compiled graph is valid for exactly one workspace. `graph_worker` caches per
  `workspace_path` — never cache one globally.

---

## 4. Context

```python
# <agent>_v2/context.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class MyContext:
    workdir: str
```

- The field **must** be named `workdir` — it is what makes your dataclass structurally compatible
  with `AuraContext` (`aura/state.py:28`), which the SessionFactory passes as `context_schema` so
  one context object feeds the entire agent tree.
- Tools read it as `runtime.context.workdir` via `ToolRuntime[MyContext]`. The model never sees it.
- Only add fields if your own middleware reads them. `iris_v2/context.py` is the one agent that
  does (it carries `auto_delegate` and the BYOK model fields because BOSS is a sub-supervisor).

---

## 5. Tools

### 5.1 The one rule that will bite you

> **NEVER put `from __future__ import annotations` in a module that defines `@tool` functions or
> the helpers they import.**

PEP-563 stringifies annotations, and `ToolRuntime[MyContext]` injection resolves by *runtime type*.
Stringified, injection silently stops working — the model is asked to supply `runtime` as a
parameter and every call fails. This is documented at the top of nearly every tool module in the
repo (`chem_v2/tools/_util.py:10`, `mach_v2/tools/control.py:7`, `builder_v2/tools/_dispatch.py:7`).
`agent.py`, `context.py`, and `prompt.py` *may* use it — tool modules and their `_util.py` may not.

### 5.2 Tool shape

```python
import logging
from typing import Optional

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from ._enums import MyModeLit          # Literal[...] — constrains the model
from ._util import _run_script
from ..context import MyContext


@tool
async def do_the_thing(
    target: str,
    runtime: ToolRuntime[MyContext],
    mode: MyModeLit = "fast",
    extra: Optional[list[str]] = None,
) -> dict:
    """One-line summary the model reads to decide whether to call this.

    Then the decision guide: when to use it, what it writes, what to do next,
    and any non-obvious parameter reasoning. This docstring IS prompt text —
    write it for the model, not for a developer.

    Args:
        target: workspace-relative path.
        mode: ...
    """
    return await _run_script(
        script_name="thing.py",
        argv=["--target", target, "--mode", mode],
        workdir=runtime.context.workdir,
        metadata={"tool": "do_the_thing"},
    )
```

- **Return a `dict`**, conventionally `{"ok": bool, "outputs": {...}, "error": ...}`. Tool results
  are re-serialized into the model's context, so keep them small and strict-JSON safe (sanitize
  `NaN`/`Inf` → `None`; see `mach_v2/tools/_util.py:_json_safe` — raw `NaN` makes the Gemini API
  reject the whole request).
- **Use `Literal` enums, not free strings**, for anything with a fixed vocabulary
  (`mach_v2/tools/_enums.py` is the exemplar). This is how you stop the model inventing options.
- **Docstrings are the routing logic.** Most of the reliability work in this repo lives in tool
  docstrings, not in the system prompt.
- **`parse_docstring=True`** is used where Google-style `Args:` should become the JSON schema
  descriptions (`@tool(parse_docstring=True)` in `chem_v2/tools/dynamic.py`).

### 5.3 The four execution shapes for a tool

| Shape | Use when | Exemplar |
|---|---|---|
| **In-process** — plain Python, no dispatch | pure lookups, formatting, HTTP calls, small parsing | `hermes_v2/tools.py`, `chem_v2/tools/delegate.py` |
| **Direct binary dispatch** — build `CommandSpec(argv=["gmx", ...])` | one scientific binary per tool | `gromacs_v2`, `qe_v2/tools/_util.py:run_pw_command`, `cfd_v2` |
| **Script dispatch** — `argv=["python3", "<baked script>.py", "--flags"]` | deterministic multi-step compute you want version-controlled and testable outside the agent | `mach_v2/tools/_util.py:_run_script` → `subagents/mach/scripts/*.py` |
| **Op-registry RPC** — one worker module, op name in argv, args as JSON on stdin | dozens of small ops sharing one heavy import (RDKit/OpenMM startup cost) | `builder_v2/tools/_dispatch.py` → `python3 -m builder_worker <op>` |

Scripts and worker modules are **baked into the SIF** via `%files` and put on `PYTHONPATH`, and the
helper resolves a local checkout path when `AURA_EXEC_BACKEND=<runtime>=local` for dev
(`mach_v2/tools/_util.py:get_scripts_path`).

### 5.4 The tool collector

`tools/__init__.py` does two jobs: tag tools with catalog metadata (drives the UI tool drawer /
MCP manifest) and expose a single collector the factory calls.

```python
from aura_framework.core.mcp.native_meta import IN_PROCESS, tag_tools
from aura_framework.core.gen_ui.tools import GEN_UI_TOOLS
from aura_framework.subagents.research import research_internet

_SIF = "my.sif"

tag_tools([do_the_thing, ...], category="pipeline", sif=_SIF, runtime="my",
          tags={"do_the_thing": ["keyword", "keyword"]})
tag_tools([research_internet], category="research", sif=IN_PROCESS, runtime=None,
          tags={"research_internet": ["web-search", "internet"]})

def create_my_v2_tools() -> list:
    return [do_the_thing, ..., finish, research_internet, *GEN_UI_TOOLS]
```

Conventional extras most agents include: `research_internet` (shared Tavily-backed web research,
bounded by `ResearchCallCapMiddleware`) and `*GEN_UI_TOOLS` (render tables/charts/molecules
directly). **Do not** add `narrate` / `annotate_artifact` — `NarrationMiddleware` contributes those.
**Do not** re-implement `write_todos`, `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`,
`execute` — deepagents provides them via the backend.

### 5.5 Control tools

`finish(summary, artifacts)` (`mach_v2/tools/control.py`, `chem_v2/tools/control.py`) validates that
every claimed artifact actually exists before the agent may claim success — an honesty guard.
It is **framework-optional**: a deep agent terminates when the model emits no tool calls; deepagents
has no built-in finish tool. `mach_v2/agent.py` documents it as "known scaffolding, not
validated-correct". Include it if you want artifact validation; otherwise let no-more-tool-calls
terminate and say so in the prompt.

Other control patterns in-tree: background jobs + `wait_for_jobs` (`chem_v2`, `qe_v2/tools/jobs.py`)
for long compute that must survive a turn boundary.

---

## 6. What you get for free — the shared middleware stack

`subagents/lean_common.py` is the shared core of every agent. **Read it before writing anything.**
`make_agent_middleware(model, backend, ...)` returns, outermost → innermost:

| Middleware | What it does |
|---|---|
| `ModelSelectionMiddleware` | swaps in the per-user / BYOK model from `runtime.context` at call time |
| `CappedSummarizationMiddleware` | compacts history at a fixed 500K tokens (keeps last 100K); evicted history is offloaded to `/conversation_history/{thread_id}.md` and stays readable via `read_file` |
| `NarrationMiddleware` | contributes the `narrate` + `annotate_artifact` tools and the unconditional narration rule that drives the live activity feed |
| `DateGroundingMiddleware` | injects today's date on every model call (per-request, not per-build) |
| `SearchPrivacyMiddleware` | forbids sending secrets/private data to web search |
| `ResearchCallCapMiddleware` | caps `research_internet` at 5 calls/run |
| `TurnToolBudgetMiddleware` | *(opt-in via `tool_budget=`)* soft nudge + hard stop on tool calls per turn |
| `*extra` | your agent-specific middleware (e.g. `AutoDelegateMiddleware`, `ImageWindowMiddleware`) |
| `SpokeSelfValidationMiddleware` | *(`self_validate=True`)* on a no-tool natural stop, loops the model back once to confirm it did not fabricate results it only *described* |
| `ToolErrorMiddleware` | innermost — turns any tool exception into an error `ToolMessage` the model can self-correct from, instead of crashing the graph. Re-raises `GraphBubbleUp` so interrupts still work |

It also registers a **harness profile** per resolved model spec that (a) disables deepagents' auto
general-purpose subagent and (b) excludes the default 85%-of-window summarizer. Because those two
are coupled, always go through `make_agent_middleware` — never wire a summarizer by hand.

Writing your own middleware? Subclass `AgentMiddleware` and implement both the sync and async hooks
(`wrap_tool_call`/`awrap_tool_call`, `wrap_model_call`/`awrap_model_call`, `after_model`/`aafter_model`).
The repo has working examples of prompt injection, per-run call caps, approval interrupts, and
`jump_to` control flow — all in `lean_common.py`.

---

## 7. Execution — containers, SIFs, and the compute plane

### 7.1 The filesystem/shell backend

`make_lean_backend(workspace_path=..., runtime=...)`:

- **`runtime="<name>"` → `SifSandbox`.** The native `execute` shell **and every file op**
  (`ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`) run *inside* the SIF through the same
  CommandSpec dispatch the typed tools use. One kernel boundary (`--containall`), no agent-driven
  I/O on the host orchestrator. The SIF's own `/opt` and `/usr` (forcefields, basis sets,
  pseudopotentials) are directly browsable. `/app` (Aura source) is **not** bound in.
- **`runtime=None` → `WorkspaceFilesystemBackend`.** Host-side, path-confined
  (`virtual_mode=True`), **no `execute` tool**. Use only for pure-HTTP agents (hermes).

Baked-in safety you inherit: search roots default to `/workspace` (a bare `glob("**/*")` cannot walk
the multi-GB image root), scans are SIGKILLed at 10s, writes outside `/workspace` fail fast with a
corrected path, and `.aura/` + `logs/` are hidden from discovery.

### 7.2 CommandSpec — the universal work order

`core/command_spec.py`. A tool never runs a binary; it *describes* one:

```python
spec = CommandSpec(
    runtime="my",                 # selects the queue + the SIF
    workdir=runtime.context.workdir,
    argv=["python3", "/app/.../script.py", "--flag", "v"],
    env={},
    sandbox_policy="strict",       # strict | compute (default) | permissive
    timeout=1800.0,                # backend SIGKILLs the process group on expiry
    resources=Resources(cpus=4, memory_gb=8, gpus=0),   # drives SLURM vs ephemeral routing
    metadata={"tool": "do_the_thing", **delegation_metadata()},
)
result = await get_backend_for_runtime("my").arun(spec)   # -> ExecutionResult
```

`ExecutionResult` gives you `ok`, `returncode`, `stdout_path`, `stderr_path`, `log_path`,
`duration`, `outputs`, and `file_changes` (a workspace snapshot diff that drives the activity
timeline). Always surface `stderr_path` to the model on failure — it is how the agent debugs itself.

Backend resolution order (`core/backends/__init__.py`): runtime-specific override →
default override → `AURA_EXEC_BACKEND` env → `rabbitmq`. For local dev,
`AURA_EXEC_BACKEND=my=local` runs the command on the host with no container.

Routing: `resources.gpus > 0` or `mpi_ranks > 1` or `cpus > 4` or `memory_gb > 8` → SLURM;
otherwise the RabbitMQ ephemeral pool. Trust tier `t2` (user-uploaded SIFs) is clamped to STRICT
and routed to a dedicated queue.

### 7.3 The SIF (`containers/sif/<name>.def`)

One Apptainer image per runtime, built from the repo root. Anatomy (see `mach.def`, `chem.def`):

```
Bootstrap: docker
From: debian:stable-slim

%labels      Runtime <name>, Version, Description
%files       aura_framework/subagents/<name>/scripts /app/aura_framework/subagents/<name>/scripts
%post        apt deps → install uv → uv venv /opt/python → uv pip install <stack>
             strip __pycache__, chmod -R a+rX, mkdir -p /opt/aura/data, rm -rf /root/.cache
%environment export PATH="/opt/python/bin:$PATH"
             export MPLBACKEND=Agg
             export AURA_DATA_DIR=/opt/aura/data
             export PYTHONPATH="/app/.../scripts:$PYTHONPATH"
%runscript   exec "$@"
%test        one import/version assertion per critical library, plus a registry smoke test
```

Design principles (from `containers/sif/README.md`): every domain SIF is also a Python sandbox
(analysis stays in the same image as the simulation); no network at runtime; default reference data
is baked in and user data is bind-mounted at `/opt/aura/data`; the same SIF runs on the ephemeral
pool and on SLURM.

```bash
# build + test (from repo root — %files paths are repo-relative)
apptainer build containers/sif/my.sif containers/sif/my.def
apptainer test  containers/sif/my.sif
apptainer exec  containers/sif/my.sif python -c "import mylib; print(mylib.__version__)"
```

### 7.4 You almost certainly do NOT need a new worker service

**Queue consolidation is a hard principle: never a queue per agent.** The single `compute-worker`
service consumes `compute.jobs`, resolves the SIF from the spec's `runtime` via `SifRegistry`, and
execs it. `compose.yaml` already mounts `./containers/sif → /opt/aura/sifs/signed:ro`, so dropping a
new `.sif` in that directory plus a `DEFAULT_SIF_NAMES` entry is the whole deployment step. The
extra worker services that do exist (`compute-worker-t2`, `-t2-gpu`, `eval-worker`) exist for
*trust/tenancy* reasons, not per-agent ones. `hermes-worker` is the one legacy exception.

---

## 8. Wiring checklist

Backend — **required**:

| # | File | Change |
|---|---|---|
| 1 | `aura_framework/core/command_spec.py:48` | add your name to the `RuntimeName` Literal |
| 2 | `aura_framework/core/backends/sif_registry.py` `DEFAULT_SIF_NAMES` | `"my": "my.sif"` |
| 3 | `containers/sif/my.def` | new SIF definition (§7.3); build the `.sif` |
| 4 | `containers/sif/README.md` | add a row to the inventory + runtime→SIF mapping tables |
| 5 | `aura_framework/subagents/my_v2/` | the four files + `tools/` (§2–§5) |
| 6 | `aura_framework/core/session_factory.py` | import the factory **and** add the `_V2_FACTORIES` entry |

`_V2_FACTORIES` is the single registration point that matters most — its **description string is
the supervisor's routing prompt**:

```python
"my": (
    create_my_v2_agent,
    "One or two sentences naming the concrete capabilities, input types and verbs a user "
    "would say. Keep it multi-domain (no single-field bias) and state the boundary with "
    "adjacent agents so the supervisor does not merge responsibilities.",
),
```

Everything downstream derives from it automatically: `DEFAULT_AGENTS`, the `/agents` API,
`KNOWN_AGENTS`/`EQUIPPABLE_AGENTS` (`aura/service/routes/_artifact_routes.py`), skill/toolkit
enablement, and the graph-cache fingerprint. If two agents' descriptions overlap, add an explicit
hand-off rule to `AURA_SUPERVISOR_PROMPT` (the chem→mach descriptor/modeling split at
`session_factory.py:169` is the worked example).

Frontend — presentation only, but the agent looks broken without it:

| File | Change |
|---|---|
| `frontend/src/lib/agents/agent-meta.ts` | `AGENT_META` entry (label, role, icon) |
| `frontend/src/lib/agent-colors.ts` | `AGENT_COLORS` + `getAgentLabel` entry |
| `frontend/src/components/icons/agent-icons.tsx` | SVG icon + registry entry |
| `frontend/src/components/onboarding/data/agents.ts`, `thread/agent-showcase.tsx`, `thread/prompt-library.tsx` | optional marketing/onboarding copy and starter prompts |

---

## 9. Verification

```bash
# 1. the graph compiles and the supervisor wires it
python -c "
from aura_framework.core.session_factory import SessionFactory, DEFAULT_AGENTS
import asyncio; print(DEFAULT_AGENTS)
print(asyncio.run(SessionFactory().build(workspace_path='/tmp/ws')))"

# 2. lint
ruff check aura_framework/subagents/my_v2

# 3. tests
pytest tests/subagents/my_v2 tests/subagents/test_lean_common.py -q
pytest tests/subagents/test_v2_backend_containment.py tests/subagents/test_sif_sandbox.py -q

# 4. the container
apptainer build containers/sif/my.sif containers/sif/my.def && apptainer test containers/sif/my.sif
```

Mirror the existing test layout in `tests/subagents/my_v2/`: one `test_<category>_tools.py` per tool
module, `test_my_v2_agent.py` for the factory/wiring, and — where a v1 predecessor exists —
`test_native_arg_parity.py` / `test_dispatch_fidelity.py` to prove the ported argv is byte-identical.
Cross-cutting suites that already exist and will cover your agent for free once registered:
`test_compute_env_prompt.py`, `test_prompt_tool_rule.py`, `test_self_validation.py`,
`test_finish_tool.py`, `test_gen_ui_tools_registered.py`, `test_resource_annotations.py`.

---

## 10. Agent profiles — find the closest match

Each entry: **shape** (the architecture), **execution**, **what makes it distinctive**, and the
files to read. Match on shape, not scientific domain.

### `chem_v2` — many small typed tools over one library, + peer delegation
- **Shape:** flat spoke, ~42 tools across 17 category modules, `finish` + `chem_python_execute`.
- **Execution:** `CommandSpec(runtime="chem")` per tool; plus a code escape hatch that writes
  `.exec_<uuid>.py` to the workspace and runs it in-SIF under `sandbox_policy="strict"`.
- **Distinctive:** *peer delegation* — `tools/delegate.py` calls the Hermes agent directly (no
  CommandSpec) to resolve compound names/SMILES. Background jobs + `wait_for_jobs`. Its prompt has
  the best "Scope Boundaries" and "Example Workflows" sections in the repo.
- **Read:** `subagents/chem_v2/{agent,prompt}.py`, `tools/{__init__,_util,dynamic,delegate,control}.py`.
- **Copy it when:** your agent wraps one rich Python library with many small operations and
  sometimes needs facts from another agent.

### `gromacs_v2` — thin typed wrappers around a CLI binary
- **Shape:** flat spoke, 29 tools (`prep`, `topology`, `analysis`, `plotting`, `support`, `dynamic`).
- **Execution:** each tool builds `argv=["gmx", <subcommand>, ...]`; the SIF's bundled forcefields
  under `/usr/local/gromacs/share` are readable directly by the agent's own file tools.
- **Distinctive:** the reference case for "the domain tool *is* a command line". Also has
  plotting tools that turn `.xvg` output into images.
- **Read:** `subagents/gromacs_v2/agent.py`, `tools/{prep,analysis,dynamic}.py`.
- **Copy it when:** your domain is an installed executable with subcommands and flags.

### `qe_v2` — incremental job-builder record, then commit-and-run
- **Shape:** flat spoke, 35 tools split into `jobs` / `setup` / `structure` / `kpoints` /
  `pseudopotentials` / `relaxation` / `run` / `postprocess` / `analysis`.
- **Execution:** tools mutate a `QEJobRecord` JSON in the workspace (`create_job` →
  `set_*` → readiness check); a `run_*` tool then *deterministically renders* the Fortran input file
  and dispatches `pw.x`. The LLM never writes the input file.
- **Distinctive:** the **accumulate-state-then-render** pattern — the strongest correctness
  guarantee available for finicky input formats. Heavy output parsing (`_parse_output_quick`,
  final-structure extraction, ASE fallbacks) with output tails fed back on failure.
- **Read:** `subagents/qe_v2/tools/{jobs,_util,run}.py` — `_util.py` is the whole pattern.
- **Copy it when:** your compute needs a large, validated, multi-section input file, or many
  parameters must be set before a single expensive run.

### `qchem_v2` — same builder pattern, parser-heavy
- **Shape:** flat spoke, 44 tools; nine dedicated `parse_*` modules (NMR, EPR, excited states,
  frequencies, scans, …).
- **Distinctive:** the extreme of "the value is in parsing the output". Read it if your run
  produces one big text log carrying many extractable properties.
- **Read:** `subagents/qchem_v2/tools/{_util,pipeline,parse_*}.py`.

### `mach_v2` — a fixed pipeline of deterministic scripts
- **Shape:** flat spoke, only 7 tools: 5 pipeline stages + gated research + `finish`.
- **Execution:** each tool dispatches a version-controlled script baked into the SIF
  (`python3 /app/.../mach/scripts/train_model.py --flags`); the script emits a
  `{"success", "data", "error"}` JSON envelope on stdout that `_util._run_script` unwraps.
  **No LLM-generated code runs on the worker** — the agent supplies decisions, scripts execute them.
- **Distinctive:** stage outputs live as files (`mach/manifest.json`, `candidates.json`), not in
  graph state, so later stages re-read instead of re-deriving. Dynamic per-call timeouts. A
  *threshold-gated* `consult_research` replacing the unrestricted `research_internet`. Strong
  anti-fabrication ("honesty") rules in both prompt and tools.
- **Read:** `subagents/mach_v2/{agent,prompt}.py`, `tools/{_util,pipeline,control,_enums}.py`,
  `subagents/mach/scripts/`.
- **Copy it when:** the workflow is a known sequence of stages, each stage is a substantial
  deterministic program, and reproducibility matters more than flexibility.

### `builder_v2` — op-registry RPC for a very large tool surface
- **Shape:** flat spoke, **87 tools** across 17 modules, all thin.
- **Execution:** every tool calls `_dispatch_op(op, args, workdir)` → one `CommandSpec` running
  `python3 -m builder_worker <op>` with args as JSON on stdin. One worker module amortizes heavy
  imports across all 87 ops. Three tools are deliberately carved out to stay in-process
  (`_inprocess.py`): the python escape hatch, static template fetch, and Hermes delegation.
- **Read:** `subagents/builder_v2/tools/{_dispatch,_inprocess,__init__}.py`.
- **Copy it when:** you have dozens of small ops over a heavyweight stack, and a
  process-per-tool-call would be dominated by import time.

### `cfd_v2` — case-directory scaffolding
- **Shape:** flat spoke, 13 tools (`scaffold`, `meshing`, `solver`, `postprocess`, `utility`).
- **Distinctive:** the domain's unit of work is a *directory tree* (an OpenFOAM case), so a
  `scaffold` tool writes the skeleton and later tools operate on it in place. Coarse-grained tools
  over a large binary suite; bundled tutorials under `/usr/lib/openfoam` are browsable.
- **Read:** `subagents/cfd_v2/tools/{scaffold,solver}.py`.
- **Copy it when:** your tool's input is a conventional multi-file project layout.

### `hermes_v2` — pure HTTP, no container, with an inline subagent
- **Shape:** `runtime=None` → **no SIF, no `execute` shell**, host-side confined FS only. Declares an
  inline dict subagent (`hermes-researcher`) sharing the same tool list for deep sub-questions.
- **Distinctive (two things):** (a) the minimal agent — the one that proves the SIF is optional;
  (b) it **reuses the legacy v1 `@tool` objects wholesale** — `create_hermes_v2_tools()` splices in
  `_CHEMINF_TOOLS`, `_PROTEIN_TOOLS`, `_LITERATURE_TOOLS`, `_MATERIALS_TOOLS`, `_REACTION_TOOLS`
  from the v1 package and defines only two new `ToolRuntime[HermesContext]` wrappers for the
  workspace-writing tools. `builder_v2/tools/_inprocess.py` does the same trick by wrapping a v1
  tool's `.coroutine`. Also the exemplar for a **per-tool call cap**
  (`LiteratureSearchCapMiddleware`) as a guaranteed-termination backstop against over-searching.
- **Read:** `subagents/hermes_v2/{agent,tools}.py`, `lean_common.LiteratureSearchCapMiddleware`.
- **Copy it when:** your agent only calls web APIs / databases and runs no local compute — or when a
  v1 predecessor already has correct, tested `@tool` objects you should not rewrite.

### `iris_v2` — a sub-supervisor with domain analysts
- **Shape:** **two levels.** `iris-BOSS` is itself a deep agent with `subagents=[...]` — one analyst
  per structural domain (`biomolecule`, `crystal`, `aggregate`), each a dict spec with its own
  prompt, tool subset, and *complete* middleware stack (deepagents does not add a default stack to
  dict subagents). BOSS gets `AutoDelegateMiddleware` and **no** `self_validate`.
- **Distinctive:** analyst specs are built per agent construction (`analysts/__init__.py`) because
  `ImageWindowMiddleware` holds per-run image state — a module-level literal would leak one
  tenant's rendered images into another. Its context carries `auto_delegate` + BYOK fields.
  Visual-loop agent: renders images and feeds them back into the model's window.
- **Read:** `subagents/iris_v2/{agent,context}.py`, `analysts/{__init__,biomolecule}.py`,
  `middleware/image_window.py`.
- **Copy it when:** your domain splits into specialisms with disjoint toolsets, or you need
  per-run mutable middleware state, or the loop is see → measure → describe.

### `aura` (the supervisor) — reference for any orchestrator
- **Shape:** `create_deep_agent(subagents=[CompiledSubAgent(...)])` over `aura-base.sif` (no science
  binaries). Tools: gen-UI display only. `AutoDelegateMiddleware` + `DelegationTaggingMiddleware`,
  no `self_validate`, no tool budget (recursion limit is the backstop).
- **Read:** `core/session_factory.py` end to end.

### `custom:<slug>` — no code at all
- **Shape:** a DB-persisted `AgentDefRecord` (system prompt + model + tool-call cap) turned into a
  real deep agent by `core/custom_agents.py`, with full middleware parity, the `aura-base` backend,
  and compute supplied entirely by user-uploaded toolkit SIFs passed as `extra_tools`.
- **Copy it when:** the "new agent" is a persona plus existing tools. Users build these in Aura
  Studio (the Architect agent) — **no repo change required.** Consider this before writing a
  package: if your agent needs no new binaries, this may be the whole answer.

### Non-blueprints (do not copy)
`subagents/toybox`, `subagents/toy_supervisor` — deepagents feature harnesses (raw
`FilesystemBackend`, `skills=` param, no Aura middleware). `subagents/architect` — the Studio coding
agent, a different animal. `subagents/research` — not an agent: it exports the shared
`research_internet` tool. Every non-`_v2` package — legacy v1 graphs.

---

## 11. Which agent is closest to mine?

| If your agent… | Start from | Key file to read |
|---|---|---|
| wraps a CLI binary with subcommands | `gromacs_v2` | `tools/prep.py` |
| wraps a Python library with many small ops | `chem_v2` | `tools/__init__.py` |
| has 40+ ops over a heavyweight stack | `builder_v2` | `tools/_dispatch.py` |
| must assemble a big validated input file before one run | `qe_v2` | `tools/_util.py` |
| mostly extracts many properties from one big output log | `qchem_v2` | `tools/parse_*.py` |
| runs a fixed sequence of deterministic stages | `mach_v2` | `tools/_util.py` |
| operates on a conventional multi-file case directory | `cfd_v2` | `tools/scaffold.py` |
| only calls web APIs, no local compute | `hermes_v2` | `agent.py` |
| splits into specialisms / needs its own subagents | `iris_v2` | `analysts/__init__.py` |
| is a see→measure→describe visual loop | `iris_v2` | `middleware/image_window.py` |
| needs long-running jobs that outlive a turn | `qe_v2` / `chem_v2` | `tools/jobs.py` |
| needs an arbitrary-code escape hatch | `chem_v2` | `tools/dynamic.py` |
| has a v1 predecessor whose tools already work | `hermes_v2` | `tools.py` (`create_hermes_v2_tools`) |
| orchestrates other agents | `aura` supervisor | `core/session_factory.py` |
| is a persona over existing tools | `custom:<slug>` | `core/custom_agents.py` |

Most real agents are a **blend** — e.g. "job-builder tools like `qe_v2`, but the run stage
dispatches scripts like `mach_v2`". That is fine and expected; the shapes compose because they all
bottom out in `CommandSpec`.

---

## 12. Gotchas — the mistakes that actually happen

1. **`from __future__ import annotations` in a tool module** → `ToolRuntime` injection silently
   breaks. The single most common failure. (§5.1)
2. **Forgetting the `_V2_FACTORIES` entry** → the agent exists, compiles, and is never routed to.
3. **Forgetting the `RuntimeName` Literal** → pydantic rejects every `CommandSpec` your tools build.
4. **`self_validate=True` on something that supervises** → it loops back after every delegating turn.
5. **Renaming a factory parameter** → `TypeError` at supervisor build; the call site is fixed.
6. **A context field not named `workdir`** → breaks `AuraContext` unification for the whole tree.
7. **Caching one compiled graph across workspaces** → the backend is workspace-bound; you will
   serve one tenant's files to another.
8. **Re-implementing built-ins** (`write_todos`, file tools, `narrate`, a summarizer) → duplicate
   tools, contradictory prompts, and two summarizers fighting.
9. **Returning `NaN`/`Inf` in a tool dict** → the model API rejects the entire request. Sanitize.
10. **Unbounded search/retry** → a `glob("**/*")` walks the whole image (clamped for you, but do not
    fight it), and an un-capped external search loops forever. Add a call-cap middleware for any
    tool the model may be tempted to retry indefinitely.
11. **Writing outside `/workspace`** → the SIF root is read-only and `/tmp` is discarded between the
    cold per-call execs. Only `/workspace` persists.
12. **Assuming network inside a SIF** → there is none. No `pip install` at runtime; bake it in `%post`.
13. **Building the SIF from anywhere but the repo root** → `%files` paths are repo-relative.
14. **Adding a per-agent queue or worker service** → violates queue consolidation. (§7.4)

---

## 13. Related reading

- `aura_framework/subagents/lean_common.py` — the shared core. Non-negotiable prerequisite.
- `aura_framework/core/session_factory.py` — supervisor assembly and registration.
- `aura_framework/core/command_spec.py` — the execution contract.
- `containers/sif/README.md` — SIF inventory, build/test commands, Apptainer-vs-Podman rationale.
- `docs/NATIVE_SUBGRAPH_ARCHITECTURE.md` — state mapping and event propagation rationale.
- `docs/MACH_PORT_DOSSIER.md` — a worked v1→v2 port.
- `docs/superpowers/specs/2026-05-25-deepagents-supervisor-migration-design.md` — why the
  supervisor is a deep agent.
- `docs/superpowers/specs/2026-06-05-deepagents-orchestration-and-narration-design.md` — why
  narration is a tool call, not prose.
- `docs/superpowers/specs/2026-06-09-aura-base-runtime-design.md` — the supervisor's own SIF.
- `docs/superpowers/specs/2026-07-01-aura-studio-7.3-agent-builder-and-architect-design.md` — the
  no-code custom-agent path.

> `CLAUDE.md` points at a root `cookbook.md` for the full per-touchpoint recipe. That file is not
> present in this worktree; until it returns, this document plus §8's checklist is the working
> substitute — and §8 was re-derived from the current code, so prefer it if the two disagree.
