# Handoff: Modular Scientific Reasoning Agent

**Purpose of this file.** Everything needed to pick this project up in a fresh
chat with no prior context. Written for an assistant, not for a reader who
already knows the project. Nothing important is left implicit.

**Repo:** `math-reasoning-agent`
**Mac:** `/Users/siya/Projects/math-reasoning-agent`
**Windows (where experiments run):** `C:\Users\SiyaJethliya\math-reasoning-agent`
**Model in use:** `google_genai:gemini-3.5-flash`
**Prover in use:** `math_v2` — set `MRA_PROVER=math_v2`; the default is still
the `pipeline` baseline, on purpose (see the comment on `config.PROVER`).
**Python:** 3.10 or newer. The suite and every script run on 3.10; nothing in
the repo uses a 3.11+ API.

**Size, as of `b65744e`:** ~20,750 lines of Python outside `tests/` (~42,200
including them), 184 commits, 77 test files, 1,394 tests passing and 17
skipped offline.

> These five numbers are the only thing in this file that rots on every
> commit, and a stale size line is how a reader concludes the rest is stale
> too. Recompute rather than trust them:
>
> ```
> git rev-list --count HEAD
> git ls-files '*.py' | grep -v '^tests/' | xargs cat | wc -l
> python -m pytest -q
> ```
>
> They were last wrong by a wide margin — the line read "~9,300 lines, 27
> commits, 287 tests" against 184 commits and 1,394 tests, because the body
> of this file was kept current and the header was not.

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
scripts/      30 scripts. The ones a newcomer needs first:
                ask.py, run.py, prove.py            one question, three depths
                evaluate.py                         the 109-case regression gate
                evaluate_proofs.py                  THE benchmark runner
                read_run.py                         read a results file properly
                verify_results.py                   recompile every claimed proof
                mutate_guard.py                     break the guard, see if a
                                                    test notices
                contamination.py                    did the model memorise the
                                                    benchmark? (null baseline)
                variance.py, compare_runs.py        run-to-run movement
              `python scripts/<name>.py --help`, and each has a docstring
              whose first line says what it is for.
tests/        offline — no API key, no Lean, no network required
config.py     every switch and limit
docs/architecture.md          the authoritative design document (563 lines)
docs/consolidation-report.md  historical record
```

**Framework independence (Principle 6):** LangChain is imported in nine
files outside `tests/` — `llm/client.py`, `pipeline/harness.py`,
`math_v2/harness.py`, `math_v2/agent.py`, the four `math_v2/tools/` modules
(each needs `@tool` and `ToolRuntime`), and `scripts/probe_models.py`. This
line used to read "exactly two"; that was true before `math_v2` existed. The
principle still holds where it matters — nothing in `verifiers/`,
`retrieval/`, `eval/` or `pipeline/guard.py` imports it, so the parts that
decide what is TRUE remain framework-free. Check with:

```
grep -rn "^from langchain\|^import langchain" --include=*.py . | grep -v ^./tests
```

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

**Do not configure this from the table above. Run the checker.**

```
python scripts/check_install.py
```

It reads the four variables a run actually needs, verifies the toolchain can
COMPILE (`lean --version` exits 0 under an unconfigured elan, so exit codes
lie), and exercises BOTH compile paths — the subprocess arm and, when
selected, the REPL. Each FAIL row names the variable and what the symptom
looks like.

MEASURED, and the reason it exists: one verification run took five attempts
because the setup lives in two files with two variables in neither, and
`scripts/diagnose_lean.py` reported "Mathlib is reachable" throughout — truly,
because its probe uses the subprocess path while every real run uses the REPL.
The configuration that broke it returned `unavailable` on all 64 checkable
proofs while every other diagnostic row passed. A list of variables in prose
is one chance to be wrong per variable; the checker cannot report success on
an install that does not work.



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
| `MRA_PROVER` | `pipeline` | `pipeline` \| `agentic` \| `math_v2` — the default is the BASELINE |
| `MRA_EXEC` | — | `local` to run commands here; unset dispatches to Aura |
| `MRA_LEAN` | `lean` | Lean executable |
| `MRA_LEAN_PROJECT` | — | Lake project with Mathlib |
| `MRA_LEAN_BACKEND` | `subprocess` | `repl` for a warm session — what every recorded run used |
| `MRA_LEAN_REPL_BIN` | — | a built `repl` binary; required when the backend is `repl` |
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

**`eval/proofs.json`** — 25 proof goals in five tiers:

| Tier | n | Meaning |
|---|---|---|
| `in-mathlib` | 6 | the theorem exists in Mathlib essentially verbatim |
| `near-mathlib` | 7 | Mathlib has it in a different shape; needs a bridge |
| `novel` | 2 | not in Mathlib |
| `hard` | 5 | near-Mathlib stopped discriminating; needs real decomposition or a multi-step argument |
| `deep` | 5 | a simple statement whose proof necessarily needs advanced machinery — not one advanced methods merely shorten |

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
deep           deep-fta                    Every non-constant complex polynomial has a root
deep           deep-liouville              A bounded entire function is constant
deep           deep-nielsen-schreier       Every subgroup of a free group is free
deep           deep-sylow                  p^n | |G| implies G has a subgroup of order p^n
deep           deep-dirichlet-ap           Infinitely many primes are congruent to a mod n
```

**Why `hard` exists.** `near-mathlib` stopped discriminating — 7/7 on a
recent run with only one goal needing real mathematical insight, the rest
solved by a bridging lemma the agent could find by name. Each `hard` goal was
chosen so retrieval alone cannot supply the answer (see each goal's `note` in
`eval/proofs.json`); four need a real multi-step argument or decomposition,
and `hard-sum-odd-squares` is a deliberate control expected to be solvable.

**Why `deep` exists, and how it differs from `hard`.** `hard` asks whether
the agent can find an idea Mathlib doesn't state directly. `deep` asks a
narrower question: is there exactly ONE kind of tool that could possibly
work — a named theorem from complex analysis, topology, or a specific
group-theoretic argument — and does the agent recognise that and go get
it? Each goal is a simple, famous statement whose standard proof is not
reachable by `omega`/`ring`/`nlinarith`/induction at all. Every claim and
its Mathlib citation were checked against the local Mathlib source
directly, not assumed — see `docs/deep-tier-candidates.md`, which also
documents classical candidates considered and dropped (Fermat's
sum-of-two-squares, Bertrand's postulate, Abel–Ruffini) because they turned
out to have genuine elementary proofs or, for Abel–Ruffini, because
Mathlib's file proves only one direction of it.

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

**Do not read proving numbers out of this file.** They were transcribed here
once, went stale within a few runs, and a reader who trusted them would have
carried away a proof rate less than half the current one. Every run writes a
results file to `eval/results/`, and the way to read one is:

```
python scripts/read_run.py eval/results/<name>.json
```

which prints the tier gradient, the outcome mix per tier, and the cost — and
refuses to print a single headline rate, for the reason below.

**The one thing worth stating here, because it is the finding rather than a
number:** self-authored goals and external ones do not measure the same
thing, and the gap between them is what the project has learned.

| | self-authored tiers | external (ProofNet) |
|---|---|---|
| `mixed-1` (35 goals) | 86–100% | 29% |
| `proofnet-20-after-soundness` | — | 53% (10 of 20) |

Any single rate that averages those two is not one number badly measured, it
is two numbers added together. `read_run.py` prints them separately and says
so in a footer, which is the behaviour to preserve.

**Three caveats that travel with every proving figure this project has:**

- **ProofNet is broken often enough to change the denominator.** Upstream
  reports mistakes in 118 of its 371 entries; this project independently
  measured ~17% of the slice it touched as broken or suspect, with two
  compiler-verified refutations. `eval/proofnet-sharp.json` (ProofNetSharp,
  the corrected set) is what to run — 67 of the 182 statements previously
  used differ from the corrected text.
- **A claimed proof is not a proof until it recompiles.**
  `scripts/verify_results.py <results.json>` takes the recorded artefact to a
  fresh compiler with nothing from `math_v2` in between. Exit 0 all
  recompiled, 1 a soundness failure, 2 could not check.
- **Cost is quadratic in model calls** (r = 0.966 against calls² over 44
  goal-runs). The ProofNet-20 run cost ~1.09M input tokens per goal. Context
  trimming was fixed to clear tool *inputs* as well as results; the size of
  that saving is UNVERIFIED and needs a paid run.

### Verification (109 golden cases) — the baseline comparison

The `grp-prime-order-cyclic` pair below is the only head-to-head proving data
point the baseline ever produced, kept because it is the reason the agentic
path was pursued at all:

| Run | Result |
|---|---|
| `grp-prime-order-cyclic`, baseline | not proved, 8 model / 7 lean / 360s |
| `grp-prime-order-cyclic`, agentic | **PROVED**, 1 model / 1 lean / 60s, 3/3 reproducible |

The baseline has never been run over a full tier, so it is not a comparator
for anything in the table above.

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

## 12. Failure log — 22 defects, and how each was found

**Code review found one of them. Running the system found the rest.**

> The table below stops at 22 and the project has since found more; the
> commit messages are the complete log and each one states the measurement
> that exposed the defect. The most consequential later one is worth naming
> here because it is a soundness failure rather than a behaviour failure:
> **`sorryAx` was accepted as a proof**, and both independent guard layers
> were broken at once (see §13). It was found by running the real-Lean tests
> against a real toolchain for the first time — which is the pattern the
> whole table records: nearly everything here was found by running the
> system, and the things it cannot reach are found by running it somewhere
> new.

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

> **Everything under "Historical" below was written when it was current and
> is now roughly 150 commits old.** It is kept because the *reasoning* in it
> is still the reasoning behind code that is still there, and because §12's
> failure log refers into it. It is not a description of the present. If the
> two disagree, this subsection wins.

### Current state, as of `b65744e`

**The prover works and the remaining problem is cost, not capability.**
ProofNet sits at 53% (10 of 20) after the soundness fix; self-authored tiers
are at 86–100% and have stopped discriminating, which is why the `hard` and
`deep` tiers exist. See §10 for how to read the numbers and why there is no
single headline rate.

**The soundness hole that mattered is closed.** `by exact sorryAx _ false`
compiled and was reported TRUE — it proves any theorem. Both independent
guard layers were broken at once: the source regex `\b(sorry|admit)\b` has no
word boundary between `sorry` and `Ax`, and the output check compared against
straight quotes while Lean 4.33 emits backticks, so it had silently matched
nothing for an unknown period. The test that was supposed to cover the second
layer used a snippet that did not typecheck and had never once exercised its
own claim. Found by running `tests/test_lean_real.py` against a real
toolchain for the first time. `scripts/mutate_guard.py` now breaks 13
soundness-critical decisions on purpose and reports any that no test notices;
it currently reports no survivors, and it should be run after any change to
`verdict.py`, `log.py` or the verifier.

**The benchmark itself was part of the problem.** 67 of the 182 ProofNet
statements this project had been running differ from the corrected text in
`eval/proofnet-sharp.json`, and 18 of those had already been decided in
committed results. Run ProofNetSharp.

**Retrieval is measurably not the lever.** Only 34% of the Mathlib
identifiers cited by *accepted* proofs were ever surfaced by a search (69 of
202 across 40 proofs), while searches are 46% of all model turns and ~24M of
the 52.9M input tokens ever spent. Goals that prove search a median of 3
times; goals that fail search 8. Searching is what the agent does when stuck,
not what unsticks it. Acting on that finding is open work; the obvious lever
is spending fewer turns on search, not ranking search better.

**`scripts/prover_spike.py` is parked, deliberately.** It asks whether a
Lean-specialised prover (Goedel-Prover-V2, served locally) closes the 12
external goals this agent has failed. The scaffolding, the corpus and the
tests are all committed and working, and it costs no API tokens to run — but
it is not on the list in §14 and should not be picked up as "the cheap thing
to do next". It answers a question about MODEL CHOICE, and the project's
open questions are about this agent: what its proofs cost, whether its
benchmark is sound, and whether its numbers are contaminated. A result from
the spike would not change any of those, and a favourable one would invite a
rewrite around a different model before the current one is characterised.
Leave it until the three questions in §14 have answers.

**The open cost question.** Cost is quadratic in model calls. Context
trimming was clearing tool results while keeping tool inputs — and for this
agent the inputs *are* the proofs, so at most 22% of the available saving was
ever reclaimed. `MRA_CONTEXT_TRIM_INPUTS` now defaults on. **The size of the
saving is unmeasured**, because measuring it needs a paid run and the spend
cap is exhausted. That is the first thing to run when the cap lifts.

### Historical — the three near-mathlib traces, and what they showed

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

The previous version of this section listed six steps, all of which are now
done — including "much later: a ProofNet adapter", which has since been
built, run, and superseded by the corrected set. `docs/RUN_NEXT.md` is older
still; this list supersedes it.

**The binding constraint is the monthly spending cap, not the code.** So the
list splits into what can run today and what is waiting on money.

### Runnable now — costs no API tokens

1. **`python scripts/verify_results.py eval/results/*.json --all`** — an
   independent recompile of every claimed proof on record. Exit 1 is the
   loudest signal this repo can produce.

2. **`python scripts/retire_saturated.py`** — the self-authored tiers are at
   86–100% and mostly no longer teach anything. Decide which goals to stop
   paying for before the next paid run, not after.

3. **`python scripts/mutate_guard.py`** after any change under
   `math_v2/core/` or `verifiers/`. 13 mutations, currently no survivors.

**Not on this list, on purpose: the prover spike.** It is runnable and free,
which is exactly why it needs saying — see §13. It is a question about model
choice, and nothing below depends on the answer.

### Waiting on the spending cap

4. **Measure the context-trimming saving.** This is the highest-value paid
   run and the cheapest to interpret: same goals, `MRA_CONTEXT_TRIM_INPUTS=1`
   against `=0`, compare input tokens per goal. The mechanism is tested and
   the effect is not; it shipped as a switchable default precisely so this
   comparison is one variable.

5. **Re-establish the ProofNet number on `eval/proofnet-sharp.json`.** 67 of
   the 182 statements previously run differ from the corrected text, so the
   53% is measured against a set that is partly wrong. Until this runs, quote
   the 53% with that caveat attached or not at all.

6. **Run the contamination probe with `--decided-only`.** It needs one model
   call per goal and reports the lift of the matched-reference score over a
   null baseline built from mismatched pairings. Below 0.05 lift means the
   raw similarity is shared notation and nothing more. Until this number
   exists, "the model may have memorised ProofNet" is neither confirmed nor
   ruled out, and that is a real hole in any result quoted externally.

**Open question worth flagging in any status report:** step 6. Every external
number this project has rests on a benchmark the model may have seen, and the
probe that would settle it has been built and not run. That is the one gap
an outside reader will find first.

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
pytest                                              # offline, no key needed
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
- **Proving is characterised on self-authored goals and thinly on external
  ones.** The self-authored tiers sit at 86–100% and have stopped
  discriminating; the external number rests on 20 ProofNet goals, and 67 of
  the 182 statements in the set previously used were wrong.
- **Contamination is unmeasured.** The probe exists, with a null baseline, and
  has not been run on the decided goals. Until it has, no ProofNet number
  from this project should be quoted externally without that caveat.
- **Results are confounded.** Architecture and model size have changed
  together across runs. `scripts/compare_runs.py` and the `run` block in each
  results file exist to limit this; they do not eliminate it.
- **Retrieval ranking is syntactic.** Loogle is not semantic search; LeanDojo's
  learned retriever would rank better. But retrieval is measurably not the
  binding constraint (§13), so improving it is unlikely to move the number.
- **Cost per goal is the practical ceiling** — ~1.09M input tokens per
  ProofNet goal, growing quadratically in model calls. The trimming fix is
  shipped and its effect is unverified.

---

## 17. One-paragraph summary for a status update

A modular scientific reasoning agent where a language model decides *what to
check* and deterministic systems — SymPy for computation, Lean 4 + Mathlib for
proof — decide *what is true*. Verdicts are computed from recorded tool
executions, never from model prose, so the model cannot assert its way to a
verified answer. Verification runs at 97% on 109 cases with 100% soundness.
The proving path proves 53% of a 20-goal ProofNet slice and 86–100% of the
self-authored tiers, on `gemini-3.5-flash`; those two figures are reported
separately on purpose, because averaging them would hide the only interesting
thing about them. It uses an agentic prover that holds one conversation with
search and compile tools — an architecture adopted after the stateless
baseline was measured repeating byte-identical failed proposals. The agent
loop is bounded in wall clock, compiles and tool calls, with termination
guaranteed in code rather than requested in a prompt, and every claimed proof
can be independently recompiled from the results file. Defects are found by
running the system rather than by reading it: the most serious to date, a
`sorryAx` term that compiled and proved anything, survived because both
independent guard layers were broken at once and was caught only when the
tests were first run against a real Lean toolchain. Two things are known and
unmeasured: whether the model has memorised the benchmark, and how much the
context-trimming fix actually saves.
