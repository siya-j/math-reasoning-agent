# What I need from the Aura repo

**I do not need any source files.** I need *interfaces* — function signatures,
dataclass field names, the allowed values of a few Literals, and which methods
a couple of middleware classes implement. All of that comes out of Python's
`inspect` module without printing a single line of Aura source.

Run **Option A** if you can. It answers everything in one go.

---

## Option A — run this on the work laptop, paste the output back

Save as `aura_probe.py` in the Aura repo root and run it with the same
interpreter the app uses.

```python
"""Print the interfaces needed to write math_v2. Prints NO source code."""
import dataclasses, importlib, inspect, sys, typing


def show(label, fn):
    try:
        print(f"\n### {label}")
        fn()
    except Exception as exc:
        print(f"  !! {type(exc).__name__}: {exc}")


def sig(obj, name=None):
    try:
        print(f"  {name or obj.__name__}{inspect.signature(obj)}")
    except Exception as exc:
        print(f"  {name or obj}: <no signature: {exc}>")


def fields_of(cls):
    print(f"  {cls.__name__}:")
    if dataclasses.is_dataclass(cls):
        for f in dataclasses.fields(cls):
            d = "" if f.default is dataclasses.MISSING else f" = {f.default!r}"
            print(f"    {f.name}: {f.type}{d}")
    elif hasattr(cls, "model_fields"):                     # pydantic v2
        for n, f in cls.model_fields.items():
            print(f"    {n}: {f.annotation} = {f.default!r}")
    else:
        print(f"    (not a dataclass/model) {inspect.signature(cls)}")


def methods(cls):
    own = [n for n in vars(cls) if not n.startswith("__")]
    print(f"  {cls.__name__}  bases={[b.__name__ for b in cls.__bases__]}")
    print(f"    methods: {sorted(own)}")
    sig(cls.__init__, f"{cls.__name__}.__init__")


print("=" * 60)
print("python", sys.version.split()[0])
for pkg in ("deepagents", "langchain", "langchain_core", "langgraph", "pydantic"):
    try:
        print(f"{pkg}=={importlib.metadata.version(pkg)}")
    except Exception:
        print(f"{pkg}: not installed")

# ---------------------------------------------------------------- deepagents
def _deepagents():
    import deepagents
    sig(deepagents.create_deep_agent)
    print(f"  exports: {sorted(n for n in dir(deepagents) if not n.startswith('_'))}")
show("deepagents", _deepagents)

# --------------------------------------------------------------- lean_common
def _lean_common():
    m = importlib.import_module("aura_framework.subagents.lean_common")
    sig(m.make_agent_middleware)
    sig(m.make_lean_backend)
    print(f"  exports: {sorted(n for n in dir(m) if not n.startswith('_'))}")
    for name in ("LiteratureSearchCapMiddleware", "ResearchCallCapMiddleware",
                 "TurnToolBudgetMiddleware", "SpokeSelfValidationMiddleware"):
        cls = getattr(m, name, None)
        if cls is not None:
            methods(cls)
show("lean_common", _lean_common)

# -------------------------------------------------------------- command_spec
def _command_spec():
    m = importlib.import_module("aura_framework.core.command_spec")
    for name in ("CommandSpec", "Resources", "ExecutionResult"):
        cls = getattr(m, name, None)
        if cls is not None:
            fields_of(cls)
    rt = getattr(m, "RuntimeName", None)
    if rt is not None:
        print(f"  RuntimeName = {typing.get_args(rt)}")
    for name in ("delegation_metadata",):
        fn = getattr(m, name, None)
        if fn:
            sig(fn)
show("command_spec", _command_spec)

# ------------------------------------------------------------------ backends
def _backends():
    m = importlib.import_module("aura_framework.core.backends")
    sig(m.get_backend_for_runtime)
    print(f"  exports: {sorted(n for n in dir(m) if not n.startswith('_'))}")
show("backends", _backends)

# ------------------------------------------------------------------- chem_v2
def _chem():
    a = importlib.import_module("aura_framework.subagents.chem_v2.agent")
    fn = next(v for k, v in vars(a).items() if k.startswith("create_") and callable(v))
    sig(fn)
    print(f"  agent.py imports: {sorted(k for k, v in vars(a).items() if inspect.ismodule(v))}")

    c = importlib.import_module("aura_framework.subagents.chem_v2.context")
    for name, obj in vars(c).items():
        if dataclasses.is_dataclass(obj):
            fields_of(obj)

    t = importlib.import_module("aura_framework.subagents.chem_v2.tools")
    coll = next(v for k, v in vars(t).items() if k.startswith("create_") and callable(v))
    sig(coll)
    print(f"  tool count: {len(coll())}")
    print(f"  tool names: {sorted(getattr(x, 'name', str(x)) for x in coll())}")

    u = importlib.import_module("aura_framework.subagents.chem_v2.tools._util")
    print("  _util public callables:")
    for k, v in vars(u).items():
        if callable(v) and not k.startswith("_") and getattr(v, "__module__", "") == u.__name__:
            sig(v, k)
    print("  _util private helpers (dispatch usually lives here):")
    for k, v in vars(u).items():
        if callable(v) and k.startswith("_") and getattr(v, "__module__", "") == u.__name__:
            sig(v, k)
show("chem_v2", _chem)

# ------------------------------------------------------------------- finish
def _finish():
    m = importlib.import_module("aura_framework.subagents.chem_v2.tools.control")
    for k, v in vars(m).items():
        if k.startswith("_"):
            continue
        target = getattr(v, "coroutine", None) or getattr(v, "func", None) or v
        if callable(target):
            sig(target, k)
            doc = (getattr(v, "description", None) or getattr(target, "__doc__", "") or "")
            print(f"    description: {doc.strip()[:400]}")
show("chem_v2 finish", _finish)

# ------------------------------------------------------------ native_meta
def _meta():
    m = importlib.import_module("aura_framework.core.mcp.native_meta")
    sig(m.tag_tools)
    print(f"  IN_PROCESS = {getattr(m, 'IN_PROCESS', '<missing>')!r}")
show("native_meta.tag_tools", _meta)

# --------------------------------------------------------- session_factory
def _factory():
    m = importlib.import_module("aura_framework.core.session_factory")
    reg = getattr(m, "_V2_FACTORIES", {})
    print(f"  registered agents: {sorted(reg)}")
    if "chem" in reg:
        entry = reg["chem"]
        print(f"  entry type: {type(entry).__name__}, length {len(entry)}")
        print(f"  entry[0]: {getattr(entry[0], '__name__', entry[0])}")
        print(f"  entry[1] (routing description): {entry[1]!r}")
    sf = getattr(m, "SessionFactory", None)
    if sf and hasattr(sf, "_build_supervisor"):
        sig(sf._build_supervisor, "SessionFactory._build_supervisor")
show("session_factory", _factory)

# ------------------------------------------------------------- sif_registry
def _sif():
    m = importlib.import_module("aura_framework.core.backends.sif_registry")
    print(f"  DEFAULT_SIF_NAMES = {getattr(m, 'DEFAULT_SIF_NAMES', '<missing>')}")
show("sif_registry", _sif)

print("\n" + "=" * 60)
print("done")
```

```bash
python aura_probe.py > aura_probe.txt 2>&1
```

Paste `aura_probe.txt` back. Check it before sending — it should contain only
names, types and one routing description, no logic.

---

## Option B — attach these files, paste this prompt

### Files to attach

Seven required. Each maps to a question in the prompt below.

| # | Path | Answers |
|---|---|---|
| 1 | `aura_framework/subagents/lean_common.py` | Q1, Q2, Q3 |
| 2 | `aura_framework/core/command_spec.py` | Q4 |
| 3 | `aura_framework/subagents/chem_v2/tools/_util.py` | Q5 |
| 4 | `aura_framework/subagents/chem_v2/agent.py` | Q6 |
| 5 | `aura_framework/subagents/chem_v2/tools/__init__.py` | Q7 |
| 6 | `aura_framework/subagents/chem_v2/tools/control.py` | Q8 |
| 7 | `aura_framework/core/session_factory.py` | Q9 |

Four optional — attach if easy, skip if not:

| Path | Adds |
|---|---|
| `aura_framework/core/mcp/native_meta.py` | `tag_tools`'s real signature rather than one inferred from a call site |
| `aura_framework/core/backends/__init__.py` | `get_backend_for_runtime`'s signature and the resolution order |
| `aura_framework/core/backends/sif_registry.py` | `DEFAULT_SIF_NAMES`, needed for wiring step 2 |
| `aura_framework/subagents/chem_v2/context.py` | confirms the context really is just `workdir` |

`AGENT_BLUEPRINT.md` is already in that repo, so the assistant can read it
without an attachment — but say so in case it needs pointing at it.

> **The point of attaching files rather than sending them.** The assistant
> reads them *there*; what comes back here is a page of signatures. Nothing
> proprietary needs to leave the machine.

### The prompt

> I am writing a new agent package in this repo, `math_v2`, following
> `AGENT_BLUEPRINT.md` (in this repo — read it first). The attached files are
> the ones the blueprint points at.
>
> I need an **interface summary**, not code. Do not paste function bodies, and
> do not reproduce any file. Quote only signatures, field names, types and
> literal values. Where I ask *how* something works, two or three sentences of
> plain description is exactly right — no source.
>
> Answer in this order, with a heading per question:
>
> 1. `lean_common.make_agent_middleware` and `make_lean_backend` — full
>    signatures with defaults, and what each returns.
> 2. `LiteratureSearchCapMiddleware` — `__init__` signature, which middleware
>    hooks it overrides, and **how it stops a tool call once the cap is hit**:
>    does it return an error `ToolMessage`, raise, or use `jump_to`? This one
>    matters most — describe the mechanism precisely.
> 3. `TurnToolBudgetMiddleware` — the same, if it exists. Note whether the
>    "hard stop" actually prevents the call or only instructs the model to stop.
> 4. `CommandSpec`, `Resources` and `ExecutionResult` — every field name, type
>    and default. The allowed values of the `RuntimeName` Literal. Whether
>    `CommandSpec` is a dataclass or a pydantic model.
> 5. `chem_v2/tools/_util.py` — the helper a tool calls to run something.
>    Its name, exact signature, what it returns, and how a failure reaches the
>    model (does it surface `stderr_path`?). Is it `async`?
> 6. `chem_v2/agent.py` — the factory's full signature, and the exact keyword
>    arguments passed to `create_deep_agent`, in order.
> 7. `chem_v2/tools/__init__.py` — `tag_tools`'s signature, the import paths
>    for `IN_PROCESS`, `GEN_UI_TOOLS` and `research_internet`, and what the
>    collector function returns.
> 8. `chem_v2/tools/control.py::finish` — its signature, its return shape, and
>    how it validates that a claimed artefact actually exists.
> 9. `session_factory.py` — one `_V2_FACTORIES` entry quoted verbatim
>    (`chem` is fine), and the exact keyword arguments the supervisor uses to
>    call a factory.
> 10. Versions of `deepagents`, `langchain`, `langgraph` and Python.
>
> Finally: name anything in these files that a new agent **must** do and that
> the blueprint does not already state, especially anything that fails
> silently rather than loudly.

---

## Option C — if you can only answer a few

Ranked. The first three unblock the most.

| # | Question | Blocks |
|---|---|---|
| 1 | `create_deep_agent(...)` kwargs as `chem_v2` calls them, and `make_agent_middleware` / `make_lean_backend` signatures | `agent.py` — the whole package will not build without it |
| 2 | `CommandSpec` + `ExecutionResult` field names; `RuntimeName` values | every dispatched tool |
| 3 | The `_util.py` dispatch helper: name, signature, return | `tools/proving.py`, `tools/symbolic.py` |
| 4 | `tag_tools` signature + the three import paths | `tools/__init__.py` |
| 5 | `finish`'s signature and return shape | `control.py` — ours diverges anyway, so I only need the house shape to match |
| 6 | How the cap middleware *stops* a tool call | the budget middleware |
| 7 | One `_V2_FACTORIES` entry, verbatim | registration |

---

## What I will do with each answer

| Answer | Used for |
|---|---|
| factory kwargs, middleware builders | `math_v2/agent.py` — ~45 lines, `self_validate=True` (leaf spoke) |
| `CommandSpec` fields, `RuntimeName` | the Lean dispatch: `argv=["lake","env","lean",...]`, `timeout`, `Resources(memory_gb=8)` — note anything >8 GB routes to SLURM, which matters because Mathlib is memory-hungry |
| `_util` dispatch helper | wrapping `math_v2/core/proving.py` and `core/symbolic.py`, which are written and tested already |
| `tag_tools`, import paths | `tools/__init__.py` catalog + collector |
| `finish` shape | `control.py`, which calls our `core/verdict.refuse()` and **will not accept a proof no recorded compilation supports** — a deliberate divergence from the optional house version |
| cap middleware mechanism | `middleware/budget.py`, keeping the two-stage STOP-then-raise so termination stays a property of code |

Nothing here needs proprietary logic. If any single answer is awkward to get,
skip it and send the rest — I will mark that piece as unverified rather than
guess at it.
