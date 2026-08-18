# Where the benchmark time actually goes

Diagnosis only. No execution architecture has been changed.

Question asked: **is our benchmark runtime measuring the reasoning agent, or
mostly measuring repeated Lean process startup?**

Answer: **roughly half of a hard goal's wall clock is Mathlib being re-imported,
and none of the remaining half is theorem checking.** Real elaboration is under
2% of the run.

---

## 1. CURRENT ARCHITECTURE

Traced through the code, not assumed.

```
scripts/evaluate_proofs.py
  └─ pipeline.proving.prove()                      config.PROVER == "math_v2"
       └─ math_v2.harness.prove()
            ├─ log.clear(workdir)
            ├─ budget.reset(workdir)               <- THE CLOCK STARTS HERE
            ├─ get_model()
            ├─ build_agent()                       langchain create_agent
            └─ _invoke(agent, goal, workdir)       <- UNBOUNDED. see §3
                 │
                 └─ the agent loop, one tool at a time:
                      tool -> budget.spend(...)    <- the ONLY enforcement point
                           -> math_v2.core.*
                           -> math_v2.tools._util.lean_runner(workdir)
                                └─ _local.run(["lake","env","lean",<file>],
                                              timeout=_aura.DEFAULT_TIMEOUT)
                                     └─ subprocess.run(...)   ONE FRESH PROCESS
```

### Every Lean-touching tool, and what it costs

| tool | `lake env lean` processes | notes |
|---|---|---|
| `check_statement` | **1** | compiles the signature with `sorry` |
| `try_standard_tactics` | **1** | ~30 tactics in one `first \| t1 \| t2 \| …` — already batched |
| `try_proof` | **1** | 0 if the identical source was already rejected (dedup exists) |
| `try_lemma` | **1** | |
| `try_skeleton` | **1** | |
| `proof_state` | 0 | reads the log |
| `search_mathlib` | 0 | Loogle over HTTP, outside the container |
| `check_*` (SymPy) | 0 Lean | separate `math_worker` subprocess |

Five call sites in `math_v2/core/proving.py` invoke `run_lean`, and every one
of them goes through `_util.lean_runner` → `_local.run` → **a brand-new
`subprocess.run`**. There is no process reuse anywhere. There is no `import
Mathlib` caching between calls, because there is no "between calls" — each call
is a fresh OS process with a fresh Lean environment.

### Lifecycle for one hard goal, concretely

```
t=0     budget.reset                      clock starts
t=0     model + agent constructed
t≈5     check_statement          PROCESS 1   lake+lean startup 10s, import 35s
t≈50    (model turn ~30s)
t≈80    search_mathlib                       HTTP, ~1-3s
t≈85    (model turn)
t≈115   try_standard_tactics     PROCESS 2   startup 10s, import 35s, tactics ~5s
t≈165   (model turn)
t≈195   try_proof                PROCESS 3   startup 10s, import 35s, elaborate ~5s
t≈245   (model turn)
t≈275   try_proof                PROCESS 4   ...
        ...
```

Processes 2, 3 and 4 each re-import the same 8,690 `.olean` files that
process 1 already imported. Nothing is carried forward.

---

## 2. WHERE THE TIME IS GOING

### First: the measurement script's own arithmetic was wrong

`scripts/time_lean.py` printed "cost of importing Mathlib 85.1s". **That number
is not trustworthy** and I should have caught it. Look at the readings:

```
  empty file                   8.1s
  import Mathlib              93.2s
  import + one theorem        43.1s     <- strictly MORE work, LESS time
```

Case 3 does everything case 2 does plus elaborate a theorem, and finishes in
less than half the time. The only explanation is the OS page cache: case 2
cold-reads several GB of oleans from disk, case 3 finds them in RAM. The script
subtracts a cold reading from a cold reading, which is meaningless. Both of your
runs show the same inversion, so this is systematic, not noise.

**The honest steady-state numbers are the third row:**

```
  lake + lean process startup      ~10s   (row 1, warm)
  Mathlib import                   ~35s   (row 3 minus row 1)
  elaborating a trivial theorem     <1s
  --------------------------------------
  ONE COMPILE                      ~45s   of which ~45s is fixed overhead
```

So a compile costs ~45 seconds **before Lean looks at your proof at all**, and
that 45s is paid again on every single invocation.

### Budget for a hard goal (8 compiles, ~10 model turns)

```
                            seconds    share    what it is
  Mathlib import            8 x 35 =    280      45%    RE-PAID OVERHEAD
  lake/lean startup         8 x 10 =     80      13%    RE-PAID OVERHEAD
  actual theorem checking   8 x ~2 =     16       2.5%  the thing we want
  model calls              10 x 30 =    300      48%    reasoning + latency
  retrieval (Loogle)        8 x ~2 =     16       2.5%
  ------------------------------------------------------
  TOTAL                                ~692
```

```
  Mathlib import   ############################################   45%
  model calls      ################################################ 48%
  lake startup     #############                                  13%
  real proving     ##                                              2.5%
  retrieval        ##                                              2.5%
```

**58% of a hard goal is Lean process and import overhead. 2.5% is theorem
checking.** The benchmark is currently measuring your disk and your model's
latency, in roughly equal parts, with the prover as a rounding error.

Note also the second-order effect, which matters more than the percentage: at
45s per compile the *budget* can only afford a handful, so the agent gives up
before it has attempted much. The overhead does not just slow the measurement
down, it changes what is measured.

---

## 3. ROOT CAUSE OF THE 1032s OVERRUN

This is a **different bug** from the import overhead, and it is not any of the
first four hypotheses. Traced:

**Not** "the timeout applies only to the agent invocation". `budget.reset()` is
called at `harness.py:124`, before `get_model()`, before `build_agent()`, before
`_invoke`. The clock covers everything.

**Not** "cleanup/finalisation outside the timer". `seconds` is
`time.monotonic() - started` measured across the whole of `prove()`.

**Partly** "individual Lean calls can run beyond the overall budget" — true but
small. `_local.run` passes `timeout=_aura.DEFAULT_TIMEOUT` (180s) to
`subprocess.run`, so one compile is hard-capped at 180s. A compile started at
t=250 can therefore end at t=430. That is an overshoot of ~130s, not 730s.

**The actual cause is that budget enforcement is *sampled*, not continuous.**

```python
# math_v2/core/budget.py
def spend(workdir, *, lean=False, search=False, ...):
    data, state = _state(workdir)
    kind, message = _over(state, lean)      # <- time is CHECKED here
```

`_over` reads the clock, but `spend()` is only ever called **from inside a
tool**. Time that passes when no tool is running is invisible to the budget
until the next tool call — at which point it has already been spent.

And the thing that runs between tool calls is **the model call**.

For `exercise_1_19b` the trace reads:

```
statement check: does NOT elaborate    (compile 1)
statement check: does NOT elaborate    (compile 2)
statement check: does NOT elaborate    (compile 3)
budget: time budget spent (1031s of 300s)
agent failed:
```

Three compiles at ~100s (cold cache) put the third one ending around t≈330.
The next budget observation was at **t=1031**. So ~700 seconds elapsed between
the third statement check returning and the next tool call, with no tool
running — and then the agent raised an exception whose message is empty, and
`0 model` was reported because `_count_model_calls` reads the *result*, which
the exception path never produced.

That signature — long silence, empty-message exception, model count of zero —
is a **client-side retry loop in the model SDK** (quota or transient error,
retried with exponential backoff, eventually raising). The budget could not
have stopped it, because the budget only ever looks at the clock when a tool
is invoked.

> **`MRA_MAX_AGENT_SECONDS` is not a wall-clock limit. It is a limit on how
> much time may have passed *when a tool is next called*.** For a run whose
> tools all behave, the two are nearly the same. For a run that stalls outside
> a tool, they are unrelated.

This is a genuine gap, it is separate from the performance question, and it is
cheap to close (see the plan).

---

## 4. CAN STATEMENT CHECKING AND PROOF CHECKING SHARE INFRASTRUCTURE?

**Yes, and they are unusually well suited to it.**

Every source this agent compiles has the same shape, built by
`core/proving.build_source`:

```lean
import Mathlib
<optional: kept lemmas>
theorem mra_goal ... := <proof>
```

The `import Mathlib` prefix is identical in every single invocation, for every
tool, for every goal. That is precisely the part that costs 35 seconds, and
precisely the part that never varies.

The Lean REPL (`leanprover-community/repl`, verified against its README)
supports exactly this via **environment snapshots**:

```json
{"cmd": "import Mathlib"}          ->  {"env": 0}      paid ONCE, ~35s
{"cmd": "theorem mra_goal ...", "env": 0}   ->  {"env": 1, "messages": [...]}
{"cmd": "theorem mra_goal ...", "env": 0}   ->  {"env": 2, "messages": [...]}
```

The isolation property is the important one for us, and it is a documented
guarantee rather than a hope: *"The `env` field … causes the command to be run
in the existing environment"* and *"You can backtrack simply by using earlier
values for `env`."* Each command **derives a new environment from env 0** and
returns a new id; it does not mutate env 0. So attempt N+1 starting from env 0
cannot observe anything attempt N defined.

Which answers the contamination constraint directly: **as long as every attempt
passes `"env": 0` and never reuses an id returned by another attempt, attempts
are as independent as separate processes.** The one thing that would break this
is deliberately threading a returned env id from one attempt into the next —
which we would simply not do, and which a test can assert.

Two documented caveats worth recording:

- Scoped environment extensions (scoped notations) do not pickle/unpickle
  correctly. Irrelevant to us — we never pickle.
- `import` is only allowed when no `env` is specified. Fits: we import once at
  session start and never again.

---

## 5. OPTIONS

### A. Reduce unnecessary Lean invocations — least invasive

| | |
|---|---|
| complexity | low; a few tens of lines |
| speedup | ~15–25% on affected goals; **zero** on well-behaved ones |
| compatibility | total, no architectural change |
| isolation | unaffected |
| benchmark-suitable | yes |

Concretely available:

1. **Cap `check_statement`.** `exercise_1_13c` spent **three** statement checks
   (~135s, 45% of a 300s budget) before attempting any mathematics. Nothing
   bounds them separately from the compile budget.
2. **Cache by source hash.** Identical source compiled twice returns the stored
   result. Attempt dedup already exists in `try_proof` but is keyed on the
   proof, not the full source, so a repeated statement check still recompiles.
3. **Fix `time_lean.py`** to discard a warm-up run, so the diagnostic stops
   producing the invalid subtraction above.

Honest assessment: these are worth doing and they do not solve the problem.
45s per compile stays 45s per compile.

### B. Batch multiple checks into one invocation

| | |
|---|---|
| complexity | low–medium |
| speedup | **~0% beyond what we already do** |
| compatibility | poor with the agent loop |
| isolation | good |
| benchmark-suitable | yes, but pointless |

**Largely already implemented.** `try_standard_tactics` puts ~30 candidate
tactics into a single `first | t1 | t2 | …` — thirty candidates, one process.
That is the batching win, and it has been taken.

Beyond that, batching is *architecturally opposed to what we are building*.
The agent's value is reading a rejection's goal state and responding to it;
attempts 2 and 3 do not exist until attempt 1 comes back. Batching them means
generating them blind, which is the pipeline prover, not the agent.

### C. Reuse a persistent Lean process — **recommended**

| | |
|---|---|
| complexity | medium; one new module behind an existing seam |
| speedup | **compile 45s → ~1–5s. Hard goal ~692s → ~330s, and the 8-compile budget becomes affordable** |
| compatibility | **excellent** — `_util.lean_runner` is already the single seam |
| isolation | strong, via `"env": 0` snapshots (documented, §4) |
| benchmark-suitable | yes; this is what LeanDojo and the Prover Agent line use |

The architecture is already shaped for this. `lean_runner(workdir)` returns an
async `(source) -> LeanResult` and is the **only** way any tool reaches Lean.
`core/proving.py` takes it as an injected argument and knows nothing about
processes. Swapping what is behind that seam touches no tool, no core proving
logic, no guard, and no classification.

Risks, stated plainly:

- **State leaking.** Mitigated by always passing `"env": 0` and never
  threading returned ids. Assertable in a test.
- **A wedged REPL.** A hung session would hang the run. Needs a per-command
  timeout and a "restart the session and retry once" path.
- **Memory.** One long-lived process holding Mathlib is ~4–6 GB. Fine for one
  goal at a time; a constraint if goals are ever parallelised.
- **The anti-cheat must not move.** `_classify` in `_util.py` checks for
  `sorry`, `axiom`, `exact?` on the way back. It runs on the source string, not
  on the process, so it is unaffected — but a REPL path must call it too, and a
  test should assert that.

### D. Lean server / LSP

| | |
|---|---|
| complexity | high |
| speedup | same as C |
| compatibility | poor |
| isolation | weaker — the LSP is built around mutable open documents |
| benchmark-suitable | not really |

The LSP exists to serve an editor: incremental edits to open files, diagnostics
pushed asynchronously, document state deliberately mutable and long-lived. We
want the opposite — a fresh derivation from a fixed base every time. Option D is
strictly more work than C for the same speedup and worse isolation. **Not
recommended.**

---

## 6. RECOMMENDATION

**Do these three, in this order. Only the third is the architecture change.**

**Step 1 — close the wall-clock gap (§3). Small, and independent of everything
else.** Wrap `_invoke` in `harness.prove` with a real deadline
(`asyncio.wait_for` at `MAX_SECONDS` plus a small margin) so that a stalled
model call cannot spend 700 unbudgeted seconds. This is a correctness fix, not
a performance one: without it no benchmark timing means anything, because any
row may silently contain an arbitrary amount of retry backoff. It does not
weaken any budget protection — it adds the one that was missing.

**Step 2 — the Option A quick wins.** Cap statement checks; cache by source
hash; fix the diagnostic script's warm-up. An hour's work, no risk.

**Step 3 — Option C, the persistent REPL, behind the existing seam and
OPT-IN.** New module `math_v2/tools/_repl.py`; `lean_runner` selects it when
`MRA_LEAN_REPL=1` and otherwise returns today's subprocess path byte for byte.
Both paths stay runnable, so the comparison is numbers rather than opinions —
and if the REPL ever behaves differently on a goal, the subprocess path is one
environment variable away.

**Why C and not "just raise the budget":** raising `MRA_MAX_AGENT_SECONDS` to
900 lets a goal finish, but a 20-goal validation run then takes 5 hours and a
183-goal test split takes two days. That is not a benchmark you can iterate on.
At ~2s per compile the same 20-goal run is under 30 minutes and the agent gets
its full 8 attempts inside a 300s budget — which is the difference between
measuring the prover and measuring the disk.

---

## 7. MINIMAL IMPLEMENTATION PLAN

Only if approved. Nothing below changes verification, classification, or the
guard.

### Step 1 — wall-clock deadline (~20 lines, `math_v2/harness.py`)

```python
result = _run_sync(asyncio.wait_for(_ainvoke(agent, goal, workdir),
                                    timeout=budget.MAX_SECONDS + margin))
```
on `TimeoutError`, `log.note(workdir, "agent exceeded the wall clock")` and fall
through to `_to_proof_run`, which already re-derives the verdict from disk. A
goal that stalls becomes `EXHAUSTED` instead of a 1032s mystery.

### Step 2 — Option A (~40 lines)

- `budget.MAX_STATEMENT_CHECKS` (default 2), charged like the others.
- `core/proving`: memoise `run_lean` on `sha256(source)` for the goal's lifetime.
- `scripts/time_lean.py`: run each case twice, report the second.

### Step 3 — persistent REPL (~200 lines, one new file)

```
math_v2/tools/_repl.py
    session(workdir)      start `lake env <repl>` once, send {"cmd":"import Mathlib"}
                          keep the returned env id as BASE
    compile(source)       strip the `import Mathlib` line,
                          send {"cmd": <rest>, "env": BASE},
                          map messages -> the SAME LeanResult the subprocess path returns
    close()               terminate on goal completion
```

`_util.lean_runner` gains one branch:

```python
if _repl.enabled():        # MRA_LEAN_REPL=1
    return _repl.runner(workdir)
```

Everything downstream — `_classify`, the anti-cheat, `interpret`, the log, the
guard, the outcome categories — is untouched, because both paths return
`LeanResult`.

---

## 8. TEST PLAN

**Correctness (offline, no Lean):**

1. `_repl.compile` returns a `LeanResult` with the same `outcome` vocabulary as
   the subprocess path, against a scripted fake REPL.
2. The anti-cheat still fires on the REPL path: `sorry` → `INCOMPLETE`,
   `axiom` → `CHEATED`. **This is the one that must never regress.**
3. A dead/wedged session yields `UNAVAILABLE`, never an exception.
4. `budget.MAX_STATEMENT_CHECKS` is enforced.
5. The wall-clock deadline produces `EXHAUSTED` and a written record.

**Isolation (with Lean, and this is the load-bearing one):**

6. Compile `theorem leaked : True := trivial`, then compile a second source
   that references `leaked`. **It must fail.** If it succeeds, attempts are
   contaminating each other and Option C is off.
7. Every `{"cmd": ...}` sent carries `"env": BASE` — asserted on the wire, so a
   future refactor cannot quietly start threading env ids.
8. A `sorry` in a skeleton behaves identically on both paths.

**Equivalence (with Lean) — the acceptance gate:**

9. Run the **7 near-Mathlib goals** on both paths. Same 7/7, same statements,
   same accepted proofs. If the REPL path proves something the subprocess path
   does not, or vice versa, it is wrong and does not ship.

**Performance:**

10. `time_lean.py --repl` reporting per-compile cost on both paths.
11. Re-run the 4 ProofNet goals both ways; compare wall clock and, more
    importantly, **compiles actually attempted per goal**. The target is not
    "faster" — it is that the agent reaches 6–8 attempts inside its budget
    instead of 2.
