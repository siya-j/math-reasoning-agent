# Tool architecture: what exists, what does not, and how to route

Written after a full inspection of the repo. The purpose is to avoid
rebuilding capabilities that are already implemented and tested, and to decide
where routing belongs.

---

## 1. Inventory — what is already built

### 1a. SymPy tools (9) — built, measured, wired to the VERIFICATION agent only

`pipeline/tools.py`, backed by `verifiers/sympy_verifier.py`.

| Tool | Kind | Decides |
|---|---|---|
| `check_equality` | EQUALITY | identities, derivatives, integrals |
| `check_numeric` | NUMERIC | a concrete expression evaluates to a number |
| `check_primality` | PRIMALITY | primality of an integer |
| `solve_equation` | SOLUTION | the solution set is exactly the candidate |
| `check_limit` | LIMIT | a limit tends to a value |
| `check_series` | SERIES | a series expansion about a point |
| `check_matrix` | MATRIX | two matrix expressions are equal |
| `check_inequality` | INEQUALITY | an inequality holds for all reals |
| `check_factorization` | FACTORIZATION | a prime factorisation is correct |

Measured at **97% verification / 100% soundness** on 109 cases. These are not
prototypes; they are the strongest-performing part of the system.

### 1b. Lean tools (3) — built, wired to the AGENTIC PROVER only

`pipeline/proof_tools.py`.

| Tool | Cost | Decides |
|---|---|---|
| `search_mathlib` | ms | Mathlib premise retrieval via Loogle |
| `try_proof` | ~20s | compiles a candidate; returns errors AND goal state |
| `try_standard_tactics` | ~20s | ~30 tactic candidates in one compile |

### 1c. Routing — built, at the PIPELINE level

`pipeline/router.py` already does the job you are describing, in code:

```
question -> interpret -> classify: COMPUTATIONAL | FORMAL | UNSUPPORTED
         -> the matching engine
         -> if nothing was settled, try the OTHER engine
```

Misrouting costs an attempt, never an answer. This is a real router, and it
predates the agentic prover.

### 1d. Built as PIPELINE STAGES, not available as tools

**This is the important row of the inventory.** Three of the four things on
your "next tools" list already exist, tested, in `pipeline/prover.py`:

| Capability | Where it lives | Available to agentic prover? |
|---|---|---|
| Auxiliary lemma generation + proving + synthesis | `_gather_lemmas`, `ProofStage.SYNTHESIS`, `MAX_LEMMAS`, `LEMMA_DEPTH` | **No** |
| Goal decomposition into `have` steps with holes | `pipeline/skeleton.py`, `_try_skeleton`, `MAX_HOLES` | **No** |
| Best-of-N draft selection | `best_draft()` — refines the fewest-error draft | **No** |
| Statement pre-flight and repair | `pipeline/statement.py` | Yes — **and the baseline lacks it** |

So the work for three of your four is **exposing existing machinery as tools**,
not building it.

### 1e. Not built

- Numerical computation beyond `check_matrix` (no `nsolve`, no numerical
  integration, no eigenvalues, no linear-algebra solving)
- Plotting or visualisation
- Proof memory across runs — `ProofLog.premises` accumulates within a single
  goal and is discarded afterwards
- Parallel best-of-N (`best_draft` is sequential, and baseline-only)
- Any SymPy access from the proving path

---

## 2. The two findings that should shape the design

### Finding 1: the agentic prover is currently fighting with one hand tied

The baseline has skeleton decomposition, best-draft refinement, and (at depth
> 0) auxiliary lemmas. The agentic prover has **none of them**. It also
ignores `depth` entirely.

This matters for the A/B experiment now queued. If the agentic prover wins
anyway, that is a strong result. **If it loses, we will not know whether that
is the architecture or the missing tools** — and the honest conclusion would
be "unmeasured", not "the pipeline is better".

### Finding 2: the two agents are disjoint, and routing happens once, up front

The verification agent has nine SymPy tools and no Lean. The prover agent has
three Lean tools and no SymPy. The router chooses between them **once**, before
either starts.

So a proof that needs a computation cannot get one. A Lean proof about a
specific integer cannot ask "is 561 prime?"; a proof needing a factorisation as
a witness cannot compute it. That is the real gap in the current design, and it
is exactly the gap your broader architecture is meant to close.

---

## 3. Design — three layers, and what decides in each

The existing split is sound and should be kept. The v2 experiment measured what
happens when the model owns control flow: coverage dropped, and nobody could
say by how much until it was measured.

| Layer | Who decides | What it decides |
|---|---|---|
| **Router** | code | which PATH — computational, formal, or both |
| **Agent** | the model | which TOOL, in what order, within a path |
| **Guard** | code | the VERDICT, from recorded executions only |

**Give the model tool choice. Do not give it the flow, and never the verdict.**

### 3a. The soundness rule that must be written down before any of this is built

> **SymPy can inform a proof. It can never establish one.**
>
> On the proving path, only a recorded Lean compilation may produce TRUE. A
> SymPy result is evidence for the model to reason with — the same status
> auxiliary lemmas already have in Phase 5, where there is an explicit test
> that evidence never changes the verdict.

Without this rule, a `compute` tool becomes a second, weaker path to TRUE, and
the entire architecture exists to prevent exactly that. Practically: SymPy
calls made during proving go into a **separate log** from Lean compilations,
and `ProofLog.accepted` continues to read only the latter.

### 3b. Tool count is not free — a measured constraint

Deep Agents with filesystem tools scored **92% against 100%**, and produced the
only soundness failure of that run. Extra tools cost restraint on abstract
claims. Handing the prover all nine SymPy tools may therefore cost more than it
buys.

*Engineering recommendation:* expose a **small number of composite tools** to
the prover rather than the full nine, and measure it as an ablation rather than
assuming. This is a hypothesis, not a conclusion — the nine tools work as well
as they do partly *because* each docstring is narrow and specific.

---

## 4. Proposed staging

Ordered by (value ÷ risk). Each stage is independently measurable.

### Stage 1 — expose what already exists (highest value, lowest risk)

Reuse the tested implementations in `prover.py` and `skeleton.py`:

- **`try_lemma(statement, proof)`** — prove an auxiliary lemma. On success it
  is added to the proof context and becomes usable by later `try_proof` calls.
  This is `_gather_lemmas` + `SYNTHESIS`, reshaped so the model decides *when*
  a lemma is worth proving instead of a fixed stage deciding for it.
- **`try_skeleton(proof_with_sorry)`** — compile a decomposition and report
  which holes remain. A skeleton that compiles with `sorry` has proved the
  decomposition typechecks, which converts one hard goal into several
  independent easy ones.

Both charge `Budget.spend(lean=True)`. Both write to `ProofLog`.

This also removes Finding 1's confound, making the A/B comparison meaningful.

### Stage 2 — cross-engine access (closes the real gap)

- **`compute(...)`** — SymPy as an oracle for the prover, under the §3a rule.

Start with the narrowest useful subset — primality, factorisation, numeric
evaluation — because those are what proofs about specific integers actually
need, and expand on evidence.

### Stage 3 — memory

- **Proof memory**: persist proved lemmas across goals and retrieve them
  alongside Mathlib premises. Genuinely new work: needs a store, a retrieval
  strategy, and a staleness policy. Highest value on the `novel` tier, where
  the same intermediate results recur.

### Stage 4 — best-of-N

Sequential first, reusing `best_draft()`'s fewest-error heuristic. Parallel
only after the sequential version shows the selection rule works — parallelism
multiplies API cost and is the easiest thing to get wrong under a rate limit.

### Not recommended yet

- Plotting/visualisation — produces artefacts a guard cannot check, so it adds
  surface area without adding a deterministic decision.
- Replacing `router.py` with agent-directed routing. The router is measured and
  cheap. Model-directed control flow is the thing that cost coverage in v2.

---

## 4b. What SymPy is actually FOR on the proving path

Not "another verifier". A different job:

> **SymPy tells you what is true. Lean proves it.**

Four concrete uses, in descending order of value:

1. **Computing a witness.** An existential goal needs one:
   `∃ p, n < p ∧ Nat.Prime p`. A CAS produces the actual prime instantly, and
   the Lean proof collapses to `⟨103, by norm_num, by norm_num⟩` instead of a
   search. This is where a CAS is unbeatable and Lean is weakest.
2. **Refuting before proving.** If SymPy finds a counterexample to an
   inequality, no proof exists and the agent should stop. Today it would spend
   eight compilations and 300 seconds discovering nothing.
   `_find_counterexample` already exists in `sympy_verifier.py`.
3. **Deciding a side condition.** "Is 561 prime?" — knowing it is 3·11·17
   tells the model which lemma to reach for and whether the claim is even true.
4. **Normal forms.** A factorisation or series expansion tells the model what
   shape the goal should reduce to before it writes `simp` and hopes.

All four are the model asking a question, not the system reaching a verdict.

---

## 4c. Where each paper's strategy lands in an agentic design

The agentic architecture does not discard these. It changes **who decides when
to apply them**. The pipeline hard-coded the order — sketch, direct, refine,
skeleton, lemmas, synthesis. The agent chooses the order instead.

**The rule that decides where each idea belongs:**

> A strategy that **generates** something becomes a TOOL the model may call.
> A constraint that **guarantees** something stays in CODE, where the model
> cannot skip it.

| Paper | Idea | Pipeline form | Agentic form |
|---|---|---|---|
| Prover Agent | informal proof as guidance | `sketch()`, one call | **subsumed** — the conversation is the sketch |
| | refinement on compiler errors | `PROOF_REFINEMENTS=3` | **already the core loop**, bounded by Budget not by stage |
| | auxiliary lemmas, bottom-up | `_gather_lemmas` | tool: `try_lemma` — model decides when |
| | refine the fewest-error draft | `best_draft()` | selection rule for best-of-N |
| | synthesis from proved lemmas | `ProofStage.SYNTHESIS` | automatic — proved lemmas sit in context |
| AxProverBase | Proposer / Compiler / Reviewer | three stages | proposer+compiler are one conversation; reviewer stays separate |
| | `sorry`/`admit`/`axiom` detection | `cheating_devices` | **stays in code.** Never a tool — a check the model can skip is not a check |
| | library search | `retrieval/` | tool: `search_mathlib` |
| LeanDojo | premise selection is the bottleneck | — | **confirmed here**: bug 20 killed 19 of 20 searches |
| | learned retriever | not built | not built — Loogle is syntactic; biggest known weakness |
| | tactic-level goal states | partial | `try_proof` returns the goal state, but not per tactic |
| miniF2F Revisited | separate formalisation from proving | — | **landed**: `statement_ok` → `NOT_FORMALIZED` |
| | LLM judges are unreliable | — | reviewer may only refuse |
| AI Co-Mathematician | hard constraints beat better prompting | the guard | **stays in code** — the guard, the Budget, anti-cheat |
| | bounded iteration prevents the death spiral | `MAX_ATTEMPTS` | the Budget, with a raise behind it |
| | reviewers can be optimised against | — | reviewer may only downgrade, never approve |

Read the right-hand column as the target design. Two entries are the whole
argument for the agentic prover — refinement stops being a fixed count of
rounds, and lemma generation stops being a stage that fires whether or not it
is wanted.

---

## 5. What this changes about the queued experiment

Nothing — run the three arms first. Stage 1 changes what the agentic prover can
do, so it must land **after** the A/B baseline is recorded, or the comparison
loses its reference point.

Then Stage 1 becomes arm D, measured against arm B on the same seven goals.
