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

## Option B — a prompt for an assistant on that machine

> I need to write a new agent package in this repo, `math_v2`, following
> `AGENT_BLUEPRINT.md`. Do not paste whole files. Answer these, quoting only
> signatures and field names:
>
> 1. `lean_common.make_agent_middleware` and `make_lean_backend` — full
>    signatures with defaults.
> 2. `LiteratureSearchCapMiddleware` — its `__init__` signature, which
>    middleware hooks it overrides, and *how it stops a tool* once the cap is
>    hit: does it return an error `ToolMessage`, raise, or use `jump_to`?
>    Describe the mechanism in one or two sentences.
> 3. `TurnToolBudgetMiddleware` — same, if it exists.
> 4. `CommandSpec`, `Resources`, `ExecutionResult` — every field name and type.
>    The allowed values of the `RuntimeName` Literal.
> 5. `chem_v2/tools/_util.py` — the helper a tool calls to run something.
>    Its name, signature, what it returns, and how it surfaces a failure
>    (`stderr_path`?) to the model. Two or three sentences, no body.
> 6. `chem_v2/agent.py` — the factory signature and the exact keyword arguments
>    passed to `create_deep_agent`.
> 7. `chem_v2/tools/__init__.py` — `tag_tools`'s signature, the import paths for
>    `IN_PROCESS`, `GEN_UI_TOOLS` and `research_internet`, and what the
>    collector returns.
> 8. `chem_v2/tools/control.py::finish` — its signature, what it returns, and
>    how it validates that a claimed artefact exists.
> 9. `_V2_FACTORIES` in `session_factory.py` — the shape of one entry, and the
>    exact keyword arguments the supervisor calls a factory with.
> 10. Versions of `deepagents`, `langchain` and Python.

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
