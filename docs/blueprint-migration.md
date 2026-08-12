# Migration to `math_v2` — remapping against AGENT_BLUEPRINT.md

**Status: analysis only. Nothing implemented.**

Source of truth: `docs/AGENT_BLUEPRINT.md` (copied into the repo so it survives
into future sessions).

---

## 1. Which existing agents to copy

The blueprint says match on **shape, not scientific domain** (§11). Our agent is
a blend, which the blueprint says is expected. Four shapes appear in it.

| Our concern | Shape | Copy from | Key file |
|---|---|---|---|
| **Overall skeleton** — flat spoke, many typed tools grouped by category, control tool | many small typed tools over one library | **`chem_v2`** | `agent.py`, `prompt.py`, `tools/__init__.py` |
| SymPy: 9+ small ops sharing one heavy import | op-registry RPC | `builder_v2` | `tools/_dispatch.py` |
| Lean: one binary with subcommands (`lake env lean`) | direct binary dispatch | `gromacs_v2` | `tools/prep.py` |
| Loogle: HTTP, no local compute | in-process | `hermes_v2` | `tools.py` |
| Assembling the `.lean` file deterministically | accumulate-then-render | `qe_v2` | `tools/_util.py` |
| Budget / call caps | per-tool call cap middleware | `hermes_v2` | `lean_common.LiteratureSearchCapMiddleware` |
| Anti-fabrication rules, file-based stage outputs | fixed deterministic stages | `mach_v2` | `prompt.py`, `tools/control.py` |

**Primary to copy line by line: `chem_v2`.** It is the closest single match —
a flat spoke wrapping a rich library with many small operations, with a
`finish` control tool and peer delegation. Read `chem_v2` first, then borrow
the four mechanics above.

Two profiles that look close but are **not** our model:

- **`mach_v2`** — a *fixed* pipeline of stages. That is our old
  `pipeline/prover.py`, which we deliberately moved away from because the fixed
  order produced byte-identical failed proposals. Borrow its honesty rules and
  its file-based stage outputs, not its shape.
- **`iris_v2`** — sub-supervisor with analysts. Only relevant if SymPy and Lean
  become separate agents. See §3.

---

## 2. The architectural tension, and how it resolves

This is the part to get right. Everything else is mechanical.

**The blueprint's model:** the agent is `create_deep_agent(...)`; the model
drives; the agent terminates when it emits no tool calls; `finish` is
explicitly *framework-optional* (§5.5).

**Our model:** the verdict is computed from recorded tool executions and the
model's prose is never consulted. This exists because of 19 measured defects,
including a v2 experiment where handing control flow to the model cost coverage.

These are compatible, but only if the guard migrates to the right place.

### 2a. Where the guard goes

The blueprint already contains the guard's shape without naming it:

> `finish(summary, artifacts)` "validates that every claimed artifact actually
> exists before the agent may claim success — an honesty guard." (§5.5)

So:

| Our component | Blueprint home | Note |
|---|---|---|
| `pipeline/guard.py` aggregation | **`finish` control tool** | reads the proof log, computes the verdict, refuses if no record supports it |
| "agent must not claim what it did not run" | `SpokeSelfValidationMiddleware` | free with `self_validate=True` |
| `pipeline/faithfulness.py` lint | inside `finish` | pure function, unchanged |
| `llm/reviewer.py` (refuse-only) | custom middleware or a `finish` sub-step | must keep the "may only downgrade" constraint |

**`finish` is optional in the blueprint and mandatory for us.** That single
sentence should go in `math_v2/agent.py` as a comment, because the next person
reading the blueprint will assume it can be dropped.

### 2b. Where the Budget goes

Ours bounds tool calls, Lean compiles, searches, consecutive searches, and wall
clock, with a `BudgetExhausted` raise behind the polite STOP. The blueprint
offers two partial matches:

- `TurnToolBudgetMiddleware` (opt-in, `tool_budget=`) — soft nudge + hard stop
  on tool calls per turn. Covers `MAX_AGENT_STEPS` only.
- `LiteratureSearchCapMiddleware` — a per-tool call cap as "a guaranteed-
  termination backstop against over-searching". Exactly our search cap.

Neither covers per-resource budgets or wall clock. The blueprint explicitly
supports writing your own (§6, "Writing your own middleware?"), and gotcha 10
says to add a call cap for any tool the model may retry indefinitely.

**Recommendation:** one `MathBudgetMiddleware` implementing all of it, modelled
on `LiteratureSearchCapMiddleware`. Keep the two-stage design — STOP message,
then raise — because that is what makes termination a property of code.

### 2c. What we lose, and should say out loud

`pipeline/router.py` (classify → engine → fall back to the other engine) has no
home inside a single agent. In the blueprint, routing between capabilities is
either the supervisor's job across agents, or the model's job via tool
docstrings. Since the user wants **one** math agent, our router's fallback
behaviour becomes prompt text.

That is a real regression risk. Failure 5 in our log was exactly this: when the
loop lived in the prompt, a small model chose not to iterate and two phases
existed in name only. **Mitigation:** if fallback matters, it belongs in
middleware, not in the prompt. Flagging it now rather than discovering it again.

---

## 3. One agent or two?

The user wants one `math_v2` that orchestrates SymPy, retrieval, Lean, proof
state, and lemmas. That is the recommendation, with one caveat.

**One agent (recommended).** SymPy and Lean are not disjoint specialisms here —
they compose *within a single problem*: compute a witness with SymPy, then cite
it in a Lean proof. Splitting them would put a supervisor hop in the middle of
one proof.

**When to revisit:** if the tool count passes ~40, or if the SymPy side grows
its own multi-step workflows, `iris_v2`'s analyst pattern becomes the answer.
Not now.

---

## 4. Component-by-component migration

`REUSE` = moves essentially unchanged. `PORT` = same logic, new shape.
`DROP` = the framework supplies it or it no longer has a job.

### Reuse as-is — pure logic, no framework coupling

| File | Lines | Becomes |
|---|---|---|
| `verifiers/sympy_verifier.py` | 357 | the SymPy op-worker's body |
| `verifiers/lean_verifier.py` | 191 | `build_source` / `rename_goal` / `declaration` / result interpretation |
| `retrieval/loogle.py` | 372 | in-process retrieval tool |
| `pipeline/tactics.py` | 97 | `try_standard_tactics` body |
| `pipeline/skeleton.py` | 85 | `try_skeleton` body |
| `pipeline/faithfulness.py` | 87 | called inside `finish` |
| `domain/` (6 files) | ~380 | shared types; `ProofRun` becomes the on-disk log schema |
| `eval/` | — | stays outside the agent package, as tests |

That is roughly **1,570 lines of tested logic that survives untouched.** This is
the `hermes_v2` lesson: reuse working tool bodies rather than rewriting them.

### Port — same logic, blueprint shape

| File | Lines | Becomes | Why it changes |
|---|---|---|---|
| `pipeline/proof_tools.py` | 421 | `tools/proving.py` | `@tool` + `ToolRuntime[MathContext]`; `ProofLog` moves to a workspace file |
| `pipeline/tools.py` | 248 | `tools/symbolic.py` | same nine tools, dispatched to the op-worker |
| `pipeline/guard.py` | 98 | `tools/control.py::finish` | verdict computed there instead of in the pipeline |
| `verifiers/lean_runner.py` | 195 | split | **parsing stays** (`_DIAGNOSTIC`, `errors`, `goals`, `cheating_devices`); **`subprocess` goes** — replaced by `CommandSpec` |
| `pipeline/statement.py` | 198 | tool + middleware | the pre-flight is a tool; the repair loop is middleware or in-conversation |
| `llm/reviewer.py` | 135 | middleware | keep "may only downgrade, never approve" |
| `config.py` | — | split | budgets → middleware; Lean paths → SIF `%environment` |

### Drop — the framework supplies it, or the job is gone

| File | Lines | Why |
|---|---|---|
| `pipeline/agentic_prover.py` | 207 | **`create_deep_agent` is the loop.** Our hand-rolled orchestration is the thing the blueprint replaces |
| `pipeline/harness.py` | 151 | the langchain/deepagents switch; deepagents is now the only harness |
| `pipeline/prover.py` | 330 | the frozen baseline. Keep in git history as the comparison; it does not migrate |
| `pipeline/proving.py` | 114 | prover selector — nothing left to select |
| `pipeline/pipeline.py`, `agent.py`, `reflection.py` | 196 | the verification outer loop → prompt workflow + middleware |
| `pipeline/router.py` | 105 | see §2c |
| `llm/client.py` | — | `model` is injected by the factory |
| `llm/retry.py` | 75 | `ToolErrorMiddleware` + framework retries |
| `llm/formalizer.py` | 327 | **the biggest conceptual change** — see below |

### The formalizer is the interesting deletion

Today `Formalizer` makes model calls *from our code*: `statement()`, `sketch()`,
`proof()`, `skeleton()`, `hole()`, `lemmas()`, `synthesis()`, `repair_statement()`.

In a deep agent **the model is the agent**. It writes the Lean statement itself,
in conversation, with the goal state and search results already in context.
There is no second model to call.

- `statement()`, `sketch()`, `proof()`, `skeleton()`, `lemmas()`, `synthesis()`
  → dissolve into the conversation. This is a genuine simplification and it is
  what the agentic prover was already moving towards.
- `repair_statement()` → dissolves too: the statement pre-flight tool returns
  Lean's error and Loogle's suggested names, and the agent revises in-context.
  The multi-round loop with memory becomes free, because conversation *is* the
  memory.
- **Keep the prompt text.** `LEAN_CONTEXT` and the "do not weaken the claim"
  rules are hard-won and move into `prompt.py`.

---

## 5. Proposed package layout

```
aura_framework/subagents/math_v2/
├── __init__.py            # re-export create_math_v2_agent
├── agent.py               # ~45 lines; comment: finish is MANDATORY here
├── context.py             # @dataclass MathContext: workdir: str
├── prompt.py              # MATH_SYSTEM_PROMPT + COMPUTE_ENV_GUIDANCE
├── middleware/
│   ├── budget.py          # MathBudgetMiddleware (§2b)
│   └── review.py          # statement-preservation, downgrade-only
└── tools/
    ├── __init__.py        # tag_tools + create_math_v2_tools()
    ├── _enums.py          # Literal types: proof stage, search mode, relation
    ├── _util.py           # CommandSpec builders, Lean/SymPy dispatch, log I/O
    ├── control.py         # finish  ← THE GUARD
    ├── symbolic.py        # the 9 SymPy tools        (op-registry dispatch)
    ├── retrieval.py       # search_mathlib           (in-process HTTP)
    ├── proving.py         # try_proof, try_standard_tactics, try_lemma,
    │                      # try_skeleton, check_statement  (binary dispatch)
    └── numeric.py         # NEW — see §6
```

Plus, outside the package:

```
aura_framework/subagents/math/scripts/math_worker.py   # SymPy op registry
containers/sif/math.def                                # Lean + Mathlib + SymPy
```

`agent.py` at ~45 lines and `prompt.py` at 150–250 lines both sit inside the
blueprint's stated ranges (§2).

---

## 6. Tools: have, port, missing

### Already built (12) — all port

| Tool | Source | Execution shape |
|---|---|---|
| `check_equality`, `check_numeric`, `check_primality`, `solve_equation`, `check_limit`, `check_series`, `check_matrix`, `check_inequality`, `check_factorization` | `pipeline/tools.py` | op-registry → `math_worker` |
| `search_mathlib` | `pipeline/proof_tools.py` | in-process HTTP |
| `try_proof`, `try_standard_tactics`, `try_lemma`, `try_skeleton` | `pipeline/proof_tools.py` | `lake env lean` dispatch |

### Missing — must be written

| Tool | Why | Risk |
|---|---|---|
| `finish` | **the guard.** Non-negotiable | — |
| `check_statement` | statement pre-flight; exists as `pipeline/statement.py` logic but not as a tool | low |
| `*GEN_UI_TOOLS` | free; covers the user's "plotting/visualisation" ask for charts and tables | none |
| `research_internet` | shared, bounded by `ResearchCallCapMiddleware`; useful for finding a theorem's standard name | low |
| **Numerical tools** (`numeric.py`) — `nsolve`, numerical integration, eigenvalues/decompositions, linear solve | the user's "numerical / linear-algebra computation" | low; SymPy/NumPy already in the SIF |
| **Proof memory** | persist proved lemmas across runs, retrieve alongside Mathlib premises | medium — needs a store and a staleness policy |
| `math_python_execute` (escape hatch, `chem_v2` shape) | flexibility for symbolic work no typed tool covers | **HIGH — see below** |

### The escape hatch is a soundness decision, not a convenience

`chem_v2` has `chem_python_execute`; the blueprint offers it as a standard
ingredient. For us it is different in kind: **arbitrary model-written Python
that produces a mathematical result is exactly the thing the guard exists to
prevent.** Our whole architecture is "deterministic systems decide correctness";
a model writing its own checker and grading itself is not that.

*Engineering recommendation:* if included, its results must be recorded as
**non-authoritative** — usable for exploration, never admissible to `finish`.
Same status SymPy has on the proving path: it informs, it never establishes.

---

## 7. The execution plane — the genuinely hard part

### 7a. `math.sif` is the biggest single task

Lean 4 + Mathlib is a multi-gigabyte toolchain and Mathlib's `.olean` cache must
be pre-built or the first import takes ~40 minutes.

```
%post   install elan → lean toolchain
        create /opt/lean-workspace, add mathlib to lakefile
        lake exe cache get        ← NETWORK, and this is fine: %post has network
        lake build
%environment
        export LEAN_WORKSPACE=/opt/lean-workspace
%test   lake env lean -e "import Mathlib" one-liner
```

Constraints from the blueprint that bite here:

- **No network at runtime (§7.3).** `lake exe cache get` must happen at build
  time. Fine.
- **Only `/workspace` is writable (gotcha 11).** So the `.lean` file is written
  into the workspace and compiled with
  `lake env lean /workspace/<thread>/claim.lean` from `/opt/lean-workspace`.
- **Memory.** Importing Mathlib needs roughly 4–8 GB. `Resources(memory_gb=8)`
  keeps us on the RabbitMQ ephemeral pool; **anything above 8 routes to SLURM**
  (§7.2). Worth measuring before choosing.
- **Timeout.** Our `LEAN_TIMEOUT=60` becomes `CommandSpec(timeout=...)`. The
  backend SIGKILLs the process group on expiry, which is stricter than our
  `subprocess` timeout and therefore an improvement.

### 7b. Loogle cannot run in the SIF

No runtime network means `search_mathlib` **must be in-process** on the
orchestrator (§5.3 "In-process — … HTTP calls", the `hermes_v2` shape). It is
already a dependency-free `urllib` client that never raises, so it ports
directly. This also keeps it off the compute queue, where a 200 ms HTTP call has
no business.

### 7c. SymPy: in-SIF, not in-process

Tempting to keep in-process — it is pure Python and fast. Recommend against it:
`parse_expr` is an eval surface, even with our 45-name allow-list, and the SIF
is exactly the containment the blueprint provides. One `math_worker` module
amortises the SymPy import across all ops (`builder_v2` pattern), which also
makes the user's "other deterministic mathematical tools" cheap to add later —
a new op, not a new dispatch path.

---

## 8. Wiring checklist — our instance of §8

| # | File | Change |
|---|---|---|
| 1 | `core/command_spec.py:48` | add `"math"` to the `RuntimeName` Literal |
| 2 | `core/backends/sif_registry.py` | `DEFAULT_SIF_NAMES["math"] = "math.sif"` |
| 3 | `containers/sif/math.def` | new; build it (§7a) |
| 4 | `containers/sif/README.md` | inventory + runtime→SIF rows |
| 5 | `subagents/math_v2/` | the package (§5) |
| 6 | `core/session_factory.py` | import factory **and** `_V2_FACTORIES["math"]` |
| 7 | `frontend/` × 3 | `AGENT_META`, `AGENT_COLORS`, icon |

Draft `_V2_FACTORIES` description — this string *is* the supervisor's routing
prompt, so it must name concrete verbs and state the boundary:

> "Symbolic and formal mathematics: evaluates and checks algebraic identities,
> derivatives, integrals, limits, series, matrices, inequalities, primality and
> factorisations with a computer algebra system; searches Mathlib for existing
> theorems; and writes and machine-checks Lean 4 proofs, reporting a claim as
> proved only when the compiler accepts it. Use for 'prove', 'verify', 'is this
> identity true', 'solve', 'simplify'. Not for numerical simulation or data
> fitting — that is mach."

The boundary sentence matters: without it the supervisor will merge us with
whatever agent owns numerical work.

---

## 9. Risks and open questions

1. **The guard must not dissolve into prompt text.** It is the reason the system
   is trustworthy. `finish` + middleware, never "the prompt says don't lie".
2. **The router's fallback has no home** (§2c). Decide deliberately: middleware,
   or accept the loss.
3. **`from __future__ import annotations` is in almost all our modules.** Gotcha
   1 says it silently breaks `ToolRuntime` injection. Every module that becomes
   a tool module or its `_util` must have it **removed** — `pipeline/tools.py`,
   `pipeline/proof_tools.py` and `retrieval/loogle.py` all currently have it.
   This is a mechanical but easy-to-miss edit.
4. **SIF build time and image size** are the schedule risk, not the Python.
5. **Memory routing.** If Mathlib needs >8 GB, every proof goes to SLURM and
   latency changes character. Measure before designing around it.
6. **Our 324 tests** target `pipeline/*`. The reused ~1,570 lines keep their
   tests; tests for dropped modules go with them. Blueprint expects
   `tests/subagents/math_v2/test_<category>_tools.py` plus
   `test_math_v2_agent.py`, and — since we have a v1 predecessor —
   `test_dispatch_fidelity.py` proving the ported argv matches.
7. **`eval/proofs.json` and `golden.json` must survive the move.** They are the
   only way to know the migration did not regress anything, and a migration
   with no before/after is how the Deep Agents filesystem regression happened.

---

## 10. Suggested order

1. Measure the current system on both datasets — the before/after reference.
2. `math.sif` + `math_worker.py`, verified with `apptainer test`. Highest risk,
   so it goes first; everything else is blocked on it.
3. `context.py`, `prompt.py`, `agent.py` — copy `chem_v2`, adapt.
4. `tools/retrieval.py` (in-process, no SIF dependency — provable early).
5. `tools/symbolic.py` over the op-worker; check against `golden.json`.
6. `tools/proving.py`; check against `proofs.json`.
7. `tools/control.py::finish` and the budget/review middleware.
8. Wiring (§8), then §9 verification.
9. Re-run both datasets. Compare to step 1.
10. Only then: numerical tools, proof memory, and the escape-hatch decision.

Steps 5, 6 and 9 are where a regression becomes visible. Do not skip 1.
