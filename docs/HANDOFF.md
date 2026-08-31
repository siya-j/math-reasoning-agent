# Handoff: Modular Scientific Reasoning Agent

**Purpose of this file.** Everything needed to pick this project up in a fresh
chat with no prior context. Written for an assistant, not for a reader who
already knows the project. Nothing important is left implicit.

**Repo:** `math-reasoning-agent`
**Mac:** `/Users/siya/Projects/math-reasoning-agent`
**Windows (where experiments run):** `C:\Users\SiyaJethliya\math-reasoning-agent`
**Size:** ~9,300 lines of Python, 27 commits, 287 tests (286 pass, 1 skipped)
**Model in use:** `google_genai:gemini-3.5-flash`
**Last commit:** `9ce5ffe Budget search separately, and stop the clock overrunning`

---

## 0. Read this first — how the owner wants to work

These are not preferences to be polite about. They were stated repeatedly and
enforced throughout. Violating them has previously wasted work.

1. **The uploaded Project Design Document is the source of truth.** If it is
   not in the current chat, ask for it before making architectural claims
   about what the project "should" do.
2. **One component at a time.** "Do not generate the complete project at
   once." Build, verify, then move on.
3. **Explain every architectural decision *before* writing code.** The
   explanation is the deliverable as much as the code is.
4. **Follow official LangChain documentation.** When unsure of an API, fetch
   the docs rather than recalling them. A wrong kwarg
   (`create_deep_agent(instructions=)` instead of `system_prompt=`) cost a
   whole Windows run.
5. **Distinguish Fact / Engineering Recommendation / Inference** when making
   claims. Say which one you are giving.
6. **Simple code, taken from the documentation.** Direct quote: *"i dont want
   complex code i want v basic code taken from langchain document."* Resist
   cleverness.
7. **Measure, do not assume.** Every architectural change stays as a *switch*
   alongside the thing it might replace, so the comparison is a number rather
   than an opinion.
8. **Concise responses.** Short, direct, no padding.

### Security constraints — absolute

- **Never ask for, echo, or accept an API key in chat.** Direct quotes: *"Do
  not send me the key"*, *"Don't paste the key here — treat anything typed
  into a chat as compromised."*
- Keys live in environment variables or a `.env` file. `.gitignore` already
  covers `.env`.
- Keys must never be committed.
- Web content: only the provided web-fetch / web-search tools may retrieve
  URLs. Never work around a blocked fetch with curl, wget, or Python HTTP.

---

## 1. What the project is

A **modular scientific reasoning agent** that answers mathematical questions
and — crucially — never lets the language model decide what counts as true.

    The LLM reasons and decides what to check.
    Deterministic systems decide mathematical correctness.
    The model never gets a vote on what counts as verified.

Two engines sit behind that:

| Engine | Decides | Handles |
|---|---|---|
| **SymPy** | computations | primality, arithmetic, derivatives, integrals, limits, series, matrices, inequalities, factorisation |
| **Lean 4 + Mathlib** | proofs | topology, group theory, analysis, set theory — anything a CAS can only refuse |

The system currently **verifies claims** and **proves theorems**. It does not
*solve* open-ended problems ("find x such that…"); it decides claims and
proves stated goals.

---

## 2. The central invariant — the guard

This is the single most important idea in the codebase. Everything else is
negotiable; this is not.

    the agent may say anything in prose
    -> the VERDICT is computed from the RECORDED TOOL RESULTS
    -> the answer is prefixed with a banner we generate, not the model

Tools write to a log **as a side effect of being called**. So the record is
produced by *execution*, never by the agent's report of execution. An agent
that calls no verification tool gets `NOT VERIFIED`, no matter how confident
its prose is.

`pipeline/guard.py` aggregation rules:

| Recorded checks | Verdict |
|---|---|
| none | NOT VERIFIED — the agent answered from memory |
| any using values the question never mentions | NOT VERIFIED — faithfulness lint |
| any FALSE | FALSE — one refutation outweighs any confirmations |
| all TRUE | TRUE |
| otherwise | UNKNOWN |

The same inversion is used on the proving path: `ProofLog.accepted` — a
recorded compilation that Lean accepted — is the **only** path to TRUE.

**Corollary that has come up repeatedly:** when adding any new capability,
ask "can the model's prose influence the verdict?" If yes, the design is
wrong.

---

## 3. Repository map

```
domain/       Check, Verdict, VerificationRequest, AgentRun, ProofRun
              (pure data, no framework code)
llm/          provider adapter — the only LangChain import lives here
              client.py, formalizer.py, interpreter.py, reviewer.py, retry.py
verifiers/    base.Verifier interface, registry
              sympy_verifier.py, lean_verifier.py, lean_runner.py
retrieval/    loogle.py — Mathlib premise search over HTTP
pipeline/     router.py      one entry point, classification + fallback
              pipeline.py    verification flow (the outer loop)
              agent.py       the agent node (one model invocation)
              guard.py       verdict from records + faithfulness lint
              tools.py       the nine verifier tools
              harness.py     langchain | deepagents switch
              reflection.py  retry policy, decided in code
              prover.py      BASELINE prover (fixed stateless sequence)
              agentic_prover.py  EXPERIMENTAL prover (one conversation)
              proof_tools.py     agent tools + Budget + ProofLog
              proving.py     prover selector + telemetry by injection
              tactics.py     deterministic tactic ladder, one compile
              skeleton.py    proof skeletons with holes
              faithfulness.py  the lint
eval/         golden.json (109 cases), proofs.json (15 goals),
              metrics.py, proof_metrics.py, runner.py, dataset.py
scripts/      ask.py, run.py, prove.py, evaluate.py, evaluate_proofs.py,
              show_proof_run.py, run_experiments.py, search_mathlib.py,
              variance.py, probe_models.py, probe_lean_model.py, check_model.py
tests/        287 offline tests — no API key, no Lean, no network required
config.py     every switch and limit
docs/architecture.md          the authoritative design document (563 lines)
docs/consolidation-report.md  historical record
```

**Framework independence (Principle 6):** exactly two LangChain imports in
the whole codebase, both in `llm/` and `pipeline/harness.py`.

---

## 4. Execution flow

```
question
  -> claim interpretation      llm/interpreter.py
  -> classification            computational | formal | unsupported
  -> SymPy path  OR  Lean path
  -> guard                     verdict from records + faithfulness lint
  -> reflection, if needed     retry, bounded, decided in code    (Phase 4)
  -> decomposition, if still unverified   auxiliary evidence      (Phase 5)
  -> answer
```

**Misrouting is survivable by design.** Classification is a model's guess. If
the first engine settles nothing, the other is tried. A claim is only reported
unsettled after both decline.

**The pipeline owns the flow; the agent owns tool choice.** This split exists
because of a measured failure: when the loop lived *inside* the agent, a small
model simply chose not to iterate, and Phases 4 and 5 existed in name only
(`mean checks per case` was 0.94). Retry and decomposition are now guaranteed
by code, not by model judgment.

---

## 5. The proving path in detail

### 5a. Baseline prover — `pipeline/prover.py` (`MRA_PROVER=pipeline`, default)

A fixed sequence of **stateless** model calls:

```
formalise  ->  CHEAP  ->  sketch  ->  DIRECT x2  ->  REFINE x3
           ->  SKELETON  ->  lemmas  ->  SYNTHESIS
```

- `CHEAP` — `pipeline/tactics.py`. One Lean compile, zero model calls. Uses
  Lean's `first | t₁ | t₂ | …` so ~30 candidate tactics cost **one**
  invocation (~20s) instead of thirty (~10 min). Candidates: 11 standard
  closers plus 9 `PREMISE_FORMS` applied to each retrieved premise.
- `sketch` — informal English proof as guidance (Prover Agent §3).
- `REFINE` — fed the previous compiler errors **including the goal state**.
- `SKELETON` — `pipeline/skeleton.py`. Asks for `have` steps with `sorry`
  holes. A skeleton that compiles *with* `sorry` has proved something real:
  the decomposition typechecks, so what remains is independent subgoals. Each
  hole gets the mechanical ladder first, costing no model call.
- `best_draft()` — refine the fewest-error draft; deliberately excludes CHEAP.

**This file is the measured baseline and must stay unchanged** unless the
change is explicitly about the baseline.

### 5b. Agentic prover — `pipeline/agentic_prover.py` (`MRA_PROVER=agentic`)

**Why it exists.** The baseline failed `grp-prime-order-cyclic` six times,
producing only two distinct proposals (attempts 2/3 byte-identical, 4/5/6
byte-identical) *even though retrieval had found the right lemma*. Root cause:
`Formalizer.proof()` is stateless — it cannot ask a follow-up question. Five
attempts are one attempt, five times.

The agentic prover holds **one conversation** with three tools and decides for
itself when to search, when to compile, and what to do with the goal state.

Three tools (`pipeline/proof_tools.py`):

| Tool | Cost | Notes |
|---|---|---|
| `search_mathlib(query)` | ms | Loogle. `\|- X` for conclusion patterns |
| `try_proof(proof)` | ~20s | the only thing that can establish a proof |
| `try_standard_tactics()` | ~20s | the mechanical ladder in one compile |

**Result on `grp-prime-order-cyclic`:** it searched three times, found the
bridge lemma `Nat.card_eq_fintype_card`, and proved it on the first compile:

```lean
by
  have : Fact (Nat.Prime (Fintype.card G)) := ⟨h⟩
  have h_card : Nat.card G = Fintype.card G := Nat.card_eq_fintype_card
  exact isCyclic_of_prime_card h_card
```

| | baseline | agentic |
|---|---|---|
| outcome | not proved | **PROVED** |
| model calls | 8 | 1 |
| Lean compiles | 7 | 1 |
| wall clock | 360s | 60s |

Reproducible 3/3.

### 5c. The Budget — `pipeline/proof_tools.py`

Giving a model the wheel means it can also drive in circles. A near-mathlib
goal ran without terminating and had to be interrupted by hand, leaving no
proof, no verdict and no record.

**Two-stage enforcement, because a polite request is not a guarantee:**

1. At the limit, every tool returns `STOP: …` instead of doing work. The agent
   gets a chance to conclude cleanly.
2. After a short grace, tools **raise** `BudgetExhausted`. `agentic_prover`
   catches it, keeps everything recorded so far (including an already-accepted
   proof), and reports honestly.

Stage 2 is what makes termination a property of the code rather than a hope
about the model. There is a test for exactly this.

**Each limit bounds only what it names.** A spent compilation budget must not
block a search. `max_tool_calls` bounds everything, so termination is
guaranteed regardless.

**Redirects vs stops.** Search has its own ceiling. Exceeding it returns
`ENOUGH SEARCHING: …` — that *tool* is spent, the *run* is not, and the
message names how many compiles remain. Redirects are charged like any other
call, so an agent that only searches is still bounded. (Making redirects free
would reintroduce the hang; there is a test for that too.)

**Clock handling.** A compile begun at 290s still runs a full 60s
`LEAN_TIMEOUT` past a 300s budget, so one is no longer *started* without time
to finish. And grace is reduced to 1 for the time budget specifically — grace
is right where saying "stop" is cheap, and wrong where each graced round trip
is spent in the very currency that ran out. Observed overshoot before this
fix: 494s against a 300s budget.

### 5d. Selector and telemetry — `pipeline/proving.py`

Instrumentation **by injection**: the baseline already accepts `formalizer`
and `check` as arguments, so its cost is counted by wrapping what is passed
in. Nothing inside `prover.py` changes.

> **Trap, already hit once:** `kwargs.setdefault("formalizer", Formalizer())`
> evaluates eagerly and builds a real model — demanding an API key even in
> offline tests. Use an explicit `if kwargs.get(...) is None:`.

> **Second trap:** `agentic_prover.prove` only builds a real model when
> `agent_factory is build_agent`. An injected factory brings its own, and a
> test must never need an API key.

---

## 6. Retrieval — `retrieval/loogle.py`

Loogle indexes Mathlib and answers over HTTP, needing neither a Lean install
nor an API key: `https://loogle.lean-lang.org/json?q=…`

**Nothing in this module raises.** A failed search is an empty result, not an
error. Retrieval is an optimisation; a network outage must never affect a
verdict.

Key functions: `conclusion_of`, `generalise`, `conclusion_pattern`,
`conclusion_patterns` (issues **both** conjunction orderings),
`hypothesis_query`, `extract_queries`, `LoogleSearch.premises_for` (a merged
query ladder, hypothesis query first).

Retrieval has produced **four** of the nineteen documented bugs. Treat it as
the most fragile component.

---

## 7. Lean integration

- `verifiers/lean_runner.py` — subprocess I/O only, decides nothing. Nothing
  raises: a missing compiler, a syntax error and a timeout are all ordinary
  outcomes reported as data.
- `verifiers/lean_verifier.py` — decides what a result *means*.

**The rule that makes Lean sound: a failed proof means UNKNOWN, never FALSE.**
Lean failing to compile a proof of P is not evidence against P. Reading
failure as refutation would make the verifier declare true theorems false.
So it returns TRUE or UNKNOWN and nothing else.

**Anti-cheat.** `sorry`, `admit`, `axiom`, `exact?`/`apply?` all compile and
prove nothing. `_uses_placeholder` and `cheating_devices` catch them.

**`lake env lean` is the only way `import Mathlib` resolves.** A bare
`lean file.lean` cannot see Mathlib however it is installed. Set
`MRA_LEAN_PROJECT` to a Lake project that depends on Mathlib.

**Error parsing.** `_DIAGNOSTIC = re.compile(r"^\S*?:\d+:\d+:\s*(error|warning):")`
captures whole **blocks**, not matching lines — an earlier version kept only
lines containing `error:` and silently discarded the `⊢ IsCyclic G` goal
state, which is the single most useful thing Lean returns.

**Windows:** subprocess must use `encoding="utf-8", errors="replace"`. Windows
decoded Lean's UTF-8 output as cp1252 and crashed a whole run.

---

## 8. Configuration — every switch (`config.py`)

| Variable | Default | Meaning |
|---|---|---|
| `MRA_MODEL` | `google_genai:gemini-3.5-flash` | provider:model |
| `MRA_HARNESS` | `langchain` | `langchain` \| `deepagents` |
| `MRA_DEEPAGENTS_FS` | off | Deep Agents filesystem tools |
| `MRA_PROVER` | `pipeline` | `pipeline` \| `agentic` |
| `MRA_LEAN` | `lean` | Lean executable |
| `MRA_LEAN_PROJECT` | — | Lake project with Mathlib |
| `MRA_LEAN_TIMEOUT` | 60 | seconds per compile |
| `MRA_MAX_AGENT_STEPS` | 20 | total agent tool calls |
| `MRA_MAX_AGENT_LEAN` | 8 | compiles per goal |
| `MRA_MAX_AGENT_SEARCHES` | 8 | searches per goal |
| `MRA_MAX_CONSECUTIVE_SEARCHES` | 3 | searches before a compile is required |
| `MRA_MAX_AGENT_SECONDS` | 300 | wall clock per goal |
| `MRA_CHECK_STATEMENT` | on | pre-flight the statement, repair once |
| `MRA_RETRIEVAL` | on | off for an ablation |
| `MRA_LOOGLE_URL` / `MRA_LOOGLE_TIMEOUT` | — | premise search |

Non-env constants: `TEMPERATURE=0.0`, `MAX_ATTEMPTS=3`, `MAX_SUBCLAIMS=4`,
`PROOF_ATTEMPTS=2`, `PROOF_REFINEMENTS=3`, `MAX_LEMMAS=3`, `LEMMA_DEPTH=1`,
`SKELETON_STEPS=4`, `MAX_HOLES=6`, `PREMISES_PER_QUERY=12`,
`MAX_PREMISE_QUERIES=4`, `PREMISE_BUDGET=24`, `CHEAP_PREMISES=12`.

---

## 9. Datasets

**`eval/golden.json`** — 109 verification cases. `scripts/evaluate.py` exits
non-zero on any soundness failure, so it works as a regression gate.

**`eval/proofs.json`** — 20 proof goals in four tiers:

| Tier | n | Meaning |
|---|---|---|
| `in-mathlib` | 6 | the theorem exists in Mathlib essentially verbatim |
| `near-mathlib` | 7 | Mathlib has it in a different shape; needs a bridge |
| `novel` | 2 | not in Mathlib |
| `hard` | 5 | near-Mathlib stopped discriminating; needs real decomposition or a multi-step argument |

```
in-mathlib     num-infinitude-of-primes    For every natural n there exists a prime p with n <= p
in-mathlib     grp-prime-order-cyclic      A finite group whose order is prime is cyclic
in-mathlib     num-two-plus-two            2 + 2 = 4
in-mathlib     num-add-zero                For every natural n, n + 0 = n
in-mathlib     num-seven-prime             7 is a prime number
in-mathlib     alg-square-nonneg           For every real x, x squared is non-negative
near-mathlib   num-primes-strictly-above   ... a prime p with n < p        (Mathlib gives <=)
near-mathlib   grp-subgroup-of-cyclic      Every subgroup of a cyclic group is cyclic
near-mathlib   ana-continuous-compact-max  A continuous real function on a compact set attains its max
near-mathlib   set-reals-uncountable       The real numbers are uncountable
near-mathlib   lin-vector-space-basis      Every vector space has a basis
near-mathlib   top-compact-image           The continuous image of a compact set is compact
near-mathlib   num-sqrt-two-irrational     The square root of 2 is irrational
novel          novel-goldbach-small        Every even 2 < n < 100 is a sum of two primes
novel          novel-sum-of-two-squares    p mod 4 = 1 implies p is a sum of two squares
hard           hard-amgm-sqrt              sqrt(ab) <= (a+b)/2 for non-negative reals a, b
hard           hard-sophie-germain         For n > 1, n^4 + 4 is not prime
hard           hard-sum-odd-squares        The sum of the first n odd numbers equals n squared
hard           hard-irrational-sqrt-sum    sqrt(2) + sqrt(3) is irrational
hard           hard-det-vanishes           The determinant of [[1,2,3],[4,5,6],[7,8,9]] is zero
```

**Why `hard` exists.** `near-mathlib` stopped discriminating — 7/7 on a
recent run with only one goal needing real mathematical insight, the rest
solved by a bridging lemma the agent could find by name. Each `hard` goal was
chosen so retrieval alone cannot supply the answer (see each goal's `note` in
`eval/proofs.json`); four need a real multi-step argument or decomposition,
and `hard-sum-odd-squares` is a deliberate control expected to be solvable.

**Tier design note.** `in-mathlib` vs `near-mathlib` was chosen so retrieval
and bridging can be measured separately. `num-primes-strictly-above` sits in
near-mathlib *specifically* because Mathlib states
`Nat.exists_infinite_primes : ∀ n, ∃ p, n ≤ p ∧ p.Prime` with `≤`, so the
strict version needs that lemma applied at `n+1`.

**Deliberately deferred: ProofNet and miniF2F.** Public benchmarks *supply*
the formal statement, so they bypass the formalizer entirely — they measure a
different system than the one being built. Not ready for these yet.

---

## 10. Measured results

Mark carefully which of these are solid and which are single runs.

### Verification (109 golden cases)

| Configuration | Verification rate | Soundness |
|---|---|---|
| LangChain harness, Gemini | 97% | 100% |
| Deep Agents harness, Gemini | 96% | 99% |
| Hybrid, 7B local | 95% | 99% |
| Deep Agents **with filesystem tools** | 92% | one soundness failure |

Deep Agents filesystem tools are **off by default** as a result. The agent has
nine verification tools and no files; the extra tools cost restraint on
abstract claims.

**Finding: Deep Agents gives no benefit here and introduced one new failure
mode.** It is kept as a switch because the company platform runs on Deep
Agents, not because it measured better.

### Proving

| Run | Result |
|---|---|
| `grp-prime-order-cyclic`, baseline | not proved, 8 model / 7 lean / 360s |
| `grp-prime-order-cyclic`, agentic | **PROVED**, 1 model / 1 lean / 60s, 3/3 reproducible |
| `in-mathlib` tier, agentic | 5/6 (one unresolved: `alg-square-nonneg`) |
| `near-mathlib` tier, agentic, depth 0 | **57% (4/7)** — see below |

**Latest near-mathlib run, in full. This run PREDATES bugs 20–22 being fixed,
and all three of its failures were caused by them — treat these numbers as a
record of the bugs, not as a measurement of the prover:**

```
num-primes-strictly-above    not proved   1 model,  0 lean, 20 retrieval,  48s
grp-subgroup-of-cyclic       PROVED       1 model,  2 lean,  5 retrieval,  65s
ana-continuous-compact-max   PROVED       1 model,  1 lean,  6 retrieval,  46s
set-reals-uncountable        PROVED       1 model,  4 lean,  1 retrieval, 103s
lin-vector-space-basis       not proved   1 model,  2 lean, 15 retrieval, 420s
top-compact-image            PROVED       1 model,  2 lean,  2 retrieval,  87s
num-sqrt-two-irrational      not proved   1 model,  4 lean,  3 retrieval, 494s
```

formalisation rate 100% · proof rate 57% · mean attempts 2.14

**Important caveat: 57% has no comparator.** The baseline has never been run
over the near-mathlib tier — there is exactly one baseline proving data point
(`grp-prime-order-cyclic`). That number should not go in front of anyone until
the baseline has run the same 7 goals.

---

## 11. Research lineage

| Paper | Idea | Where it lives |
|---|---|---|
| **Prover Agent** (2506.19923) | informal proof as guidance | `Formalizer.sketch()` |
| | refinement on compiler errors | `PROOF_REFINEMENTS` |
| | auxiliary lemmas, bottom-up | `_gather_lemmas` |
| | refine the fewest-error draft | `best_draft()` |
| | synthesis from proved lemmas | `ProofStage.SYNTHESIS` |
| **AxProverBase** (2602.24273) | `sorry`/`admit` detection | `_uses_placeholder` |
| | `axiom` and suggestion tactics | `cheating_devices` |
| | library search | `retrieval/` |
| | Proposer / Compiler / Reviewer split | `agentic_prover` + `reviewer` |
| **LeanDojo** (2306.15626) | premise selection is the bottleneck | `retrieval/loogle.py` |
| | learned retriever, tactic-level goal states | **not built** |
| **miniF2F Revisited** (2511.03108) | separate formalisation from proving when measuring | methodology |
| | 97% formalizer x 70.8% prover = **34.8%** end-to-end | expectation setting |
| | LLM judges unreliable: 97.5% vs 62.7% human | informs caution |
| **AI Co-Mathematician** (2605.06651) | hard programmatic constraints beat better prompting | the guard, anti-cheat |
| | bounded iteration prevents the death spiral | `MAX_ATTEMPTS`, the Budget |
| | reviewers can be optimised against | the reviewer constraint below |

### The reviewer constraint

`llm/reviewer.py` may **downgrade** a verdict to UNKNOWN. It may **never**
produce a TRUE. Two independent findings force this:

- *AI Co-Mathematician*: optimising against a reviewer can "converge to an
  argument that remains flawed, but where the errors can no longer be detected
  by the reviewer agent."
- *miniF2F Revisited*: an LLM judge rated formalisations 97.5% correct where
  human experts found 62.7%.

A reviewer that can only refuse is safe under both. One that can approve is a
new way to be confidently wrong.

---

## 12. Failure log — 19 defects, and how each was found

**Code review found one of them. Running the system found the rest.**

| # | Failure | Found by |
|---|---|---|
| 1 | Constant of integration treated as a counterexample | evaluation |
| 2 | Invented symbol ruled on numerically | evaluation |
| 3 | Claim substitution: "is 2 the *only* solution?" checked as "are they 2 and −2?" | evaluation |
| 4 | Docstring examples copied verbatim into unrelated questions | evaluation |
| 5 | Phases 4 and 5 silently deleted by a rewrite | evaluation (`mean checks` 0.94) |
| 6 | Oscillating limit reported as a definite value | evaluation |
| 7 | Lowercase `i` parsed as a variable, making a true claim FALSE | evaluation |
| 8 | A wrong series silently replaced with the correct one | evaluation |
| 9 | Type variable `G` wasting a premise-search slot | running it |
| 10 | Name search burying the needed theorem | running it |
| 11 | Probe reporting a conclusion when no call reached the model | running it |
| 12 | Execution-flow steps 1–2 orphaned as dead code | design-doc audit |
| 13 | Retrieval searched identifiers, not the goal's conclusion | running it |
| 14 | Conjunction order hid the needed lemma from Loogle | running it |
| 15 | Six premises per query, ranked by module order, not relevance | running it |
| 16 | `in-mathlib` goal stated `n < p` where Mathlib gives `n ≤ p` | running it |
| 17 | Verification rates counted errored cases in the denominator | running it |
| 18 | The proving path had no rate-limit backoff | a failed run |
| 19 | Windows decoded Lean's UTF-8 output as cp1252 and crashed | a failed run |
| 20 | Retrieval discarded Loogle's own spelling corrections | reading a trace |
| 21 | The goal's name shadowed the Mathlib lemma that proves it | reading a trace |
| 22 | An unelaborable statement scored as a proving failure | reading a trace |

**Failures 20–22 came out of a single set of three traces, and none was a
reasoning failure** — the agent argued correctly in all three and was defeated
by something upstream of it.

- **20.** A bare identifier is a *constant* lookup in Loogle; a name
  *fragment* must be quoted. Loogle answers an unparseable query with an error
  **and a suggestion list**, and `search()` returned `[]` for both. On
  `num-primes-strictly-above`, 19 of 20 searches returned nothing, the agent
  never compiled once, and Loogle had named `Nat.exists_infinite_primes` every
  single time. Verified against the live service.
- **21.** `theorem irrational_sqrt_two : Irrational (Real.sqrt 2)` collides
  with Mathlib's `irrational_sqrt_two`, so `exact irrational_sqrt_two` is
  simultaneously correct and impossible — the error is at *declaration*, which
  no proof can route around. The agent diagnosed it and tried `_root_.`.
  **This bug gets worse as the formalizer improves**, because the right name
  for a known theorem *is* the library's name. It targeted precisely the
  in-mathlib and near-mathlib tiers being scored. Fixed by renaming every goal
  to `mra_goal` in `build_source`.
- **22.** See §13.

Additional traps found since, worth keeping in mind:

- SymPy folds `2**3 * 3**2 * 5` into `360`, destroying factorisation
  structure. Fixed with `parse_expr(..., evaluate=False)`, which then required
  `Mul Add Pow` in the ~45-name allow-list.
- Skeleton hole indexing: filling hole 0 shifts hole 1 down to index 0. Track
  `position`, increment only on failure.
- `.gitignore` once contained `eval/last_proof_run.json` — the exact file
  needed for diagnosis. `eval/results/` is now explicitly tracked.
- The Loogle `generalise` helper duplicated blanking inside `Nat.card`,
  producing `Nat._`.

**Two meta-lessons the owner has explicitly drawn:**

1. *"A rate whose denominator includes failures-to-run will eventually lie to
   you."* This shape appeared three times (11, 17, and once in `variance.py`).
   A Deep Agents run answered 8 of 8 correctly, was rate-limited on the last
   two, and reported 80%.
2. Failure 13 was **introduced by a fix**. The conclusion-pattern change
   improved `IsCyclic` and silently broke every goal concluding a quantifier.
   *An improvement verified on one example is a hypothesis, not a fix.*

---

## 13. Where things stand right now

### Most recent work — the three near-mathlib traces, and what they showed

The traces were read and **all three failures were bugs upstream of the
agent**. All three are fixed and committed (`661e1c6`, `a1090a7`). See §12
entries 20–22 for the first two. The third:

**Bug 22 — an unelaborable statement was scored as a proving failure.**
`lin-vector-space-basis` formalised to a statement naming `Basis`, which
current Mathlib calls `Module.Basis`. Every attempt failed identically —
including `by sorry` — because the error was in the *signature*. Two
compilations were spent proving nothing, and the run still reported a
formalisation rate of **100%**, because "formalised" meant "the model returned
a non-empty string". A formalizer fault was being scored against the prover.
Same shape as failures 11 and 17.

`pipeline/statement.py` now compiles the statement with `sorry` before proving
(so the signature is the only thing under test), asks Loogle what the
unrecognised names were renamed to, and gives Lean's error plus those names
back to the formalizer for **one** repair. `ProofRun.statement_ok` feeds
`classify()`, so this outcome is now `NOT_FORMALIZED`.

Formalisation was the only stage of the pipeline with no feedback loop at all,
and it is the acknowledged weak point of the system.

> **Guardrail to preserve.** A repair may fix a NAME. It must not fix the
> MATHEMATICS — a statement quietly weakened until it compiles is failures 3
> and 8 again, and nothing in the code can detect that. The prompt forbids it,
> which is a request and not a guarantee, so **every repair is written to the
> trace verbatim** for a human to read. Do not remove that.

`pipeline/statement.py` is a new module rather than an edit to `prover.py`,
because the baseline stays frozen. Ablate with `MRA_CHECK_STATEMENT=0`.

**Nothing has been re-run yet.** All the numbers in §10 predate these three
fixes.

### Before that (commits `4e47409` and `9ce5ffe`)

The agent loop is now bounded and terminates cleanly. `near-mathlib` ran
end-to-end for the first time — 7/7 goals decided, no hang, no Ctrl+C.

Then three failure modes were identified in that run, and fixes for two of
them are committed but **not yet re-run**:

**(1) `num-primes-strictly-above` — allocation failure, now fixed.**
20 retrieval calls, 0 Lean calls. 20 is *exactly* `max_tool_calls` — the bound
fired correctly, but search was capped only by the shared total, so the cheap
action starved the only action that can prove anything. Search now has its own
ceiling plus a consecutive-search cap.

*Untested hypothesis about the mathematics:* the agent probably searched 20
times for a strict-inequality lemma that does not exist, instead of compiling
`Nat.exists_infinite_primes (n+1)`. Same shape as the
`Nat.card_eq_fintype_card` bridge the agent *did* find on
`grp-prime-order-cyclic`. **Checkable from the trace — not yet checked.**

**(2) `lin-vector-space-basis` — undiagnosed.**
15 retrieval + 2 Lean + 420s. The agent is doing work but cannot bridge the
formalisation to a verified proof. Candidate causes: wrong theorem selection,
typeclass/instance issues, failure to recognise an existing theorem, failure
to decompose, poor use of Lean feedback. **The trace is needed to know which.**

**(3) `num-sqrt-two-irrational` — clock overrun, now fixed.**
494s against a 300s budget. Also suspicious mathematically: Mathlib has
`irrational_sqrt_two` outright, so 4 compiles and no proof points at bridging
the formalised statement to `Irrational`, not at difficulty.

### Also added

Search traces now record premise **names**, not just hit counts. A count
cannot distinguish "retrieval never surfaced the lemma" from "retrieval
surfaced it and the agent ignored it", and those need opposite fixes.

### Deliberately NOT done

**The system prompt was left untouched.** Folding a strategy hint into the
same commit as the budget changes would make the next run's delta
unattributable. Strategy changes should be a separate, ablatable commit.

---

## 14. Immediate next steps, in order

1. **Re-run near-mathlib** with all three fixes in place. This is the next
   real experiment; every number in §10 predates it.

   ```powershell
   git pull
   .\.venv\Scripts\python.exe -m pytest -q
   $env:MRA_PROVER="agentic"
   .\.venv\Scripts\python.exe scripts\evaluate_proofs.py --tier near-mathlib --depth 0
   ```

   What to watch, in order of importance:
   - Does any goal make **more than one `try_proof` call**? Every agentic
     proof so far has succeeded on the first compile, so the revision loop is
     still unexercised (see below).
   - How many goals end in `Stopped early` versus a genuine not-proved.
   - How many report `NOT FORMALISED` now that bug 22 is caught — that number
     moves out of the proof rate and into the formalisation rate, so the
     headline proof rate is **not** comparable to the old 57%.
   - Any `statement repaired` line in a trace. Read the before/after by hand
     and check the mathematics was not quietly changed.

2. **Re-run in-mathlib** too. Bug 21 (the name collision) hit that tier
   hardest, so 5/6 may improve for reasons unrelated to proving skill.

3. **Run the baseline over both tiers** so the agentic numbers have a
   comparator. There is still exactly one baseline proving data point.

4. **Diagnose `alg-square-nonneg`** (in-mathlib scored 5/6 with one unknown).

5. Only after the above: strategy work, and only as separate ablatable
   commits. The system prompt is still deliberately untouched.

6. Much later: a ProofNet adapter, with the formalizer-bypass caveat stated
   explicitly whenever those numbers are quoted.

**Open question worth flagging in any status report:** every agentic proof so
far has succeeded on its *first* `try_proof` call. The revision loop — read a
failed goal state, change approach — is still essentially unexercised.
near-mathlib is where that gets tested.

---

## 15. Running it

### Setup

```bash
pip install -r requirements.txt
```

Requirements: `langchain`, `sympy`, `pydantic`, `pytest`, plus one provider
package (`langchain-google-genai` reads `GOOGLE_API_KEY`). `deepagents` is
optional and needs Python 3.11+.

Lean is installed via `elan`. Set `MRA_LEAN_PROJECT` to a Lake project that
depends on Mathlib, or every proof reports UNKNOWN (which is a correct, if
useless, answer).

### Commands

```bash
pytest                                              # 287 tests, no key needed
python scripts/ask.py "Is 561 a prime number?" --trace
python scripts/run.py "Is 7919 prime?"              # verify only
python scripts/prove.py "..." --dry-run             # prove only
python scripts/search_mathlib.py "IsCyclic"         # premise search
python scripts/evaluate.py                          # the 109 cases (regression gate)
python scripts/evaluate_proofs.py --tier near-mathlib --depth 0
python scripts/show_proof_run.py --goal <id> --full
python scripts/run_experiments.py                   # everything, unattended
```

### Windows (where the API key lives)

```powershell
git pull
.\.venv\Scripts\python.exe -m pytest -q
$env:MRA_PROVER="agentic"
.\.venv\Scripts\python.exe scripts\evaluate_proofs.py --tier near-mathlib --depth 0
```

`scripts/run_experiments.py` writes `eval/results/<timestamp>/` with a `.log`
and `.json` per step plus a `manifest.json` recording exit codes, durations
and the environment. Built for unattended runs on a machine with no other
tooling; a failing step is recorded and the run continues.

### Working arrangement

Development happens on the Mac. The Mac sandbox has **no SSH key**, so
`git push` fails there with "Host key verification failed" — the owner pushes
manually. Experiments run on the Windows company laptop, which has the API key
and **no Claude access**, so anything to be run there must be written down as
copy-pasteable commands.

---

## 16. Known limitations — state these honestly

- **Statement preservation is unsolved.** The guard proves a check passed, not
  that it matched the question. Failures 3 and 8 are the same underlying
  problem and remain open. The real fix is back-translation, which needs a
  model trustworthy enough to judge — and the literature says LLM judges
  overstate agreement by ~30 points.
- The reviewer exists and is constrained to refuse only, but **its own
  accuracy is unmeasured.**
- **Coverage.** SymPy cannot touch topology, group theory or set theory. Lean
  can, but needs a model that writes Mathlib well.
- **Proving is demonstrated, not characterised.** One tier at 57%, one at 5/6,
  no baseline comparator for either.
- **Results are confounded.** Architecture and model size have changed
  together across runs.
- **Retrieval ranking is syntactic.** Loogle is not semantic search; LeanDojo's
  learned retriever would rank better.
- **The agent's revision loop is unexercised** (see §14).

---

## 17. One-paragraph summary for a status update

A modular scientific reasoning agent where a language model decides *what to
check* and deterministic systems — SymPy for computation, Lean 4 + Mathlib for
proof — decide *what is true*. Verdicts are computed from recorded tool
executions, never from model prose, so the model cannot assert its way to a
verified answer. Verification runs at 97% on 109 cases with 100% soundness.
The proving path formalises at 100% and currently proves 57% of the
near-Mathlib tier, using an experimental agentic prover that holds one
conversation with search and compile tools — an architecture adopted after the
stateless baseline was measured repeating byte-identical failed proposals. The
agent loop is bounded in wall clock, compiles and tool calls, with termination
guaranteed in code rather than requested in a prompt. Nineteen defects have
been found and documented; eighteen were found by running the system rather
than by reading it.
