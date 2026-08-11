# Architecture and Methodology

The authoritative description of what this system is, how it works, and why
each decision was made. For the historical record of the consolidation phase,
see [consolidation-report.md](consolidation-report.md).

**Status:** ~6,000 lines of Python · 238 tests · 109 golden cases · 15 proof
goals · 9 SymPy tools · 2 LangChain imports in the architecture.

**Measured with Gemini 3.5 Flash:** verification 97% accuracy / 100%
soundness · autoformalisation 100% · the proving path produces
machine-checked Lean proofs.

---

## 1. The problem

Language models are fluent about mathematics and unreliable at it. Asked
whether 561 is prime, a model will answer confidently in the same tone
whether it is right or wrong. 561 is 3 × 11 × 17, but it is a Carmichael
number and passes the tests a model is likely to reason about.

The usual response is to make the model try harder — longer chains of
thought, self-critique, more sampling. This does not work, because the model
is also the thing grading itself. A system built that way has no way to tell
you which of its answers are the wrong ones.

## 2. The central strategy

> **Separate the thing that can be wrong from the thing that decides what is
> true.**

The model is treated as an untrusted proposer. It is good at reading a
question and choosing an approach, and structurally incapable of certifying
its own output — so it never certifies. A deterministic engine does that, and
a **guard** computes the verdict from recorded tool results rather than from
anything the model said in prose.

The test of whether this is real rather than decorative: what happens when
the model is right but did not check? The answer is stamped NOT VERIFIED. A
correct-but-uncertified answer is rejected. That rule is the whole design.

### Why verification is tractable when generation is not

Checking is easier than producing. "Is 3215031751 prime?" requires work.
"Does 61 × 52711001 equal 3215031751?" is multiplication.

The system is built on that asymmetry, and its scope follows from it. The
problems it handles are **decidable** — primality, symbolic equality, limits,
factorisation all have algorithms. Topology mostly does not, which is exactly
why those claims are refused rather than guessed at, until a proof assistant
is involved.

### Where the risk actually lives

The model still performs **translation**: from "is the derivative of x³ equal
to 3x²?" to `check_equality(lhs="diff(x**3,x)", rhs="3*x**2")`. That
translation can be wrong.

So the model's unreliability is not eliminated, it is **relocated** — from
*"is the answer correct?"*, which is invisible and unfalsifiable, to *"is this
the right question to ask?"*, which is narrow and inspectable. Failures 3 and
8 below are both translation failures. The engine was right each time.

---

## 3. Architecture

Five packages, each with one responsibility. None imports a framework except
`llm/`.

```
domain/       what things ARE            Claim, Check, Verdict, AgentRun, ProofRun
llm/          probabilistic reasoning    client, interpreter, formalizer
verifiers/    deterministic correctness  SymPy, Lean, the Verifier interface
retrieval/    library knowledge          Mathlib premise search
pipeline/     orchestration              router, guard, reflection, prover, tools
eval/         measurement                dataset, metrics, runner
```

`domain/` contains no framework code and no engine code. `verifiers/` decides
correctness and knows nothing about models. `pipeline/` owns control flow and
is the only place that knows the whole workflow.

### The LangChain surface is two imports

```python
from langchain.chat_models import init_chat_model   # llm/client.py
from langchain.agents import create_agent           # pipeline/agent.py
```

That is the entire dependency, in 5,295 lines. The architecture does not
depend on LangChain; LangChain implements the architecture. Switching
providers is one environment variable:

```bash
export MRA_MODEL="openrouter:cohere/north-mini-code:free"
export MRA_MODEL="anthropic:claude-sonnet-4-6"
export MRA_MODEL="ollama:qwen2.5:7b"
```

---

## 4. Execution flow

```
                        question (English)
                              │
                              ▼
                   llm/interpreter.py                    steps 1–2
                   interpret + classify
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
      computational                       formal
              │                               │
              ▼                               ▼
   pipeline/pipeline.py               pipeline/prover.py
   ┌────────────────────┐            ┌────────────────────┐
   │ agent picks tools  │            │ formalise          │
   │ SymPy verifies     │            │ direct proving ×2  │
   │ guard decides      │            │ refinement ×3      │
   │ reflection retries │            │ auxiliary lemmas   │
   │ decomposition      │            │ synthesis          │
   └────────────────────┘            └────────────────────┘
              │                               │
              └───────────────┬───────────────┘
                              ▼
                   fallback to the other
                   engine if unsettled
                              │
                              ▼
                          Answer
```

### Routing, and why misrouting is survivable

Classification comes from a model, so it will sometimes be wrong. The
contract is that **a wrong route costs an attempt, never an answer**: if the
first engine settles nothing, the other is tried. A claim is reported
unsettled only after both have declined.

Two guards keep that from being wasteful. Fallback does not fire when the
first engine already settled the question, and `unsupported` claims never
reach the prover. Unparseable classification defaults to `computational` —
the cheap deterministic engine — because defaulting to `formal` would spend a
proof budget on `2 + 2`.

---

## 5. The guard

`pipeline/guard.py`. Two jobs, both pure functions of recorded data. No model
is consulted.

**Aggregation.** One refutation outweighs any number of confirmations.

| Recorded checks | Verdict |
|---|---|
| none | NOT VERIFIED — the agent answered from memory |
| any using values the question never mentions | NOT VERIFIED — faithfulness lint |
| any FALSE | FALSE |
| all TRUE | TRUE |
| otherwise | UNKNOWN |

**The key inversion.** The agent node returns two channels:

```python
return log.checks, prose
```

Downstream code consumes only `log.checks`. The prose is appended for the
human and never parsed. Tools write to the log as a *side effect of being
called*, so the record is produced by execution rather than by the model's
report of execution. The model has no channel through which to assert truth.

### Faithfulness lint

The guard can prove a check *passed*. It cannot prove the check was a
translation of the question asked. `pipeline/faithfulness.py` closes part of
that gap deterministically: it compares numeric literals in the model's
transcription of the claim against those in the question. A value present in
the check but absent from the question was invented.

Only fields that should be *transcriptions* are linted —
`SOLUTION.candidate`, `SERIES.rhs`, `FACTORIZATION.rhs` — one entry per
observed substitution. Fields like `lhs` hold the expression under test and
legitimately contain derived values; linting them produced false positives.

This is a mitigation, not a solution. It cannot see semantic drift that
preserves the numbers.

---

## 6. The verification path

**Hybrid: the pipeline owns the flow, the agent owns tool choice.**

This split was not a preference; it was measured. Three architectures were
built and evaluated:

| Architecture | Accuracy | Soundness | Coverage |
|---|---|---|---|
| fixed workflow, 3B, 34 cases | 91% | 94% | 93% |
| tool-calling agent, 3B, 34 cases | 82% | 97% | 79% |
| hybrid, 7B, 87 cases | 95% | 99% | 95% |
| hybrid, 7B, 109 cases, 9 tools | 96% | 99% | 96% |
| **hybrid, Gemini 3.5 Flash, 109 cases** | **97%** | **100%** | **97%** |
| hybrid + Deep Agents, Gemini, 109 cases | 96% | 99% | 97% |

### The confound is resolved

The table above was ambiguous for most of the project: architecture and model
size had always changed together, so "did the hybrid help, or was it just a
bigger model?" was unanswerable.

Gemini on the same 109 cases settles it. **96% at 7B, 97% with a frontier
model — a one-point difference.** The architecture was doing the work, not the
model. That also means the guarantee is portable: soundness has now been
measured at 94–100% across a 3B local model, a 7B local model and a frontier
API model.

### Deep Agents: no benefit, one new failure

Same model, same cases, one variable — the harness (`MRA_HARNESS`).

Coverage held at 97%, so the feared repeat of the v2 collapse did **not**
happen. But restraint on abstract claims fell to 92%, producing the only
soundness failure of the run: `abs-continuous-differentiable` — *"is every
continuous function differentiable?"* — where the correct behaviour is to
refuse.

Different failure mode from v2, same underlying cause. Given more tools and a
filesystem, a model finds something to *do* on an unanswerable question
rather than declining. Reported here as measured rather than resolved: the
specific tool call that produced the verdict has not been inspected.

The pure agent version *silently deleted Phases 4 and 5*: the model simply
chose not to iterate, and `mean checks per case` fell to 0.94. The capability
existed on paper only.

> **Capability that must exist cannot depend on the model deciding to use it.**

So retry and decomposition moved into Python. Latest run: 13 cases recovered
by retry.

**Confounds, stated plainly.** Row 3 changes both architecture and model
size; row 4 changes both case count and tool count. Neither improvement can
be attributed to one variable.

### Reflection (`pipeline/reflection.py`)

| Verdict | Action |
|---|---|
| TRUE / FALSE | stop |
| UNKNOWN | retry — the check was probably malformed |
| NOT_APPLICABLE | nudge **once**, then stop |

Never retry on FALSE. Retrying until the verifier agrees turns a verifier
into an agreement machine. The single nudge distinguishes "forgot to check"
from "genuinely uncheckable"; more would be pressure to fabricate.

### Decomposition (`pipeline/pipeline.py`)

When a claim is unverified, auxiliary checkable facts are gathered. They land
in `state.evidence`, a separate field from `state.checks`, so they are
structurally incapable of reaching the guard. Verified special cases do not
establish a general claim.

### The nine tools

Each takes `claim` as its first argument, so the log records what the model
*thought* it was testing alongside what it actually ran.

`check_equality` · `check_numeric` · `check_primality` · `solve_equation` ·
`check_limit` · `check_series` · `check_matrix` · `check_inequality` ·
`check_factorization`

`verifiers/sympy_verifier.py` parses with a ~40-name allow-list and
`global_dict={}`, because SymPy's parser uses `eval`.

---

## 7. The proving path

`pipeline/prover.py`, following Prover Agent §3.

```
formalise → direct ×2 → refinement ×3 → lemmas (depth D) → synthesis → report
```

### The rule that makes Lean sound

> **A failed Lean proof means UNKNOWN. It never means FALSE.**

Lean failing to compile a proof of *P* is not evidence against *P* — it
usually means the proof was wrong or Mathlib spells the lemma differently.
Reading failure as refutation would make the system declare true theorems
false. `LeanVerifier` returns TRUE or UNKNOWN and nothing else.

### Compiles but proves nothing

Four ways a file can typecheck while establishing nothing, all detected:

| Device | Why it passes the compiler |
|---|---|
| `sorry` / `admit` | placeholders — exit code 0, warning only |
| `axiom` | assumes the goal instead of deriving it |
| `apply?`, `exact?`, `simp?` | suggestion tactics, not commitments |

Without these, `axiom cheat : 2 + 2 = 5` followed by `theorem t := cheat`
would be recorded as VERIFIED TRUE.

### Why lemmas may affect the outcome here

In the verification path, auxiliary checks are evidence and can never move
the verdict. In the proving path, proved lemmas **are** allowed to contribute
— because the assembled proof is itself submitted to the compiler. The lemmas
are inputs to something that gets checked, not evidence trusted on its own.
Remove the final verification step and this becomes unsound immediately.

### The mechanical attempt

Before any model call, one Lean compile tries everything obvious:

```lean
by first
  | rfl | trivial | norm_num | decide | simp | positivity | omega | linarith | aesop
  | exact Nat.exists_infinite_primes
  | apply Nat.exists_infinite_primes
  | simpa using Nat.exists_infinite_primes
  ...
```

`first | ...` commits to whichever alternative closes the goal, so ~57
candidates cost ONE invocation rather than 57. Nine standard closers plus
four forms against each of the top twelve retrieved premises.

This exists because every measured outcome turned on retrieval rather than
proof search. `Nat.exists_infinite_primes n` closes the infinitude goal with
no reasoning at all, so no model call should have been spent on it. A
consequence worth stating: goals in this class remain provable when the model
is rate limited, since only the formalisation call is needed.

### Refinement repairs the best draft

Prover Agent §3.1: refine the attempt with the **fewest** compiler errors,
not the most recent one, and send the previous attempt back alongside the
errors so the model repairs a draft rather than starting over.

---

## 8. Retrieval

`retrieval/loogle.py`. LeanDojo's central finding is that **premise selection
is the bottleneck**: a model writing Mathlib proofs from memory is guessing
among ~167,000 declarations.

Loogle answers over HTTP, so this needs neither Lean nor an API key.

**Conclusion patterns, not name search.** Measured against the live service:

| Query | Hits | Found `isCyclic_of_prime_card`? |
|---|---|---|
| `IsCyclic` | 2,163 | ❌ not in the first 200 |
| `\|- IsCyclic _` | 54 | ✅ rank 4 |

`|- X _` asks for declarations that *conclude* X, which is what proving
needs. Query extraction is deterministic — a regex over the statement's own
structure — because asking the model what to search for would put a guess in
front of the lookup that exists to replace guessing.

**The conclusion's shape, not its identifiers.** The first version of the
above extracted bare identifiers, which discards exactly the information that
matters:

| Query | Hits | Found `Nat.exists_infinite_primes`? |
|---|---|---|
| `\|- Nat.Prime _` | 31 | ❌ — it concludes an existential |
| `\|- ∃ _, _ ∧ Nat.Prime _` | 1 | ✅ |

The goal concludes `∃ p, ...`, not `Nat.Prime _`, so the needed lemma was
structurally invisible. With `PREMISES_PER_QUERY = 6` the model was shown
*"11 is prime, 5 is prime, 7 is prime"* and asked to prove there are
infinitely many. Five attempts never had a chance.

**Both conjunction orderings.** Loogle matches structurally, and a model has
no way to know Mathlib's convention:

```
the model wrote   ∃ p, Nat.Prime p ∧ n < p
Mathlib has       ∃ p, n ≤ p ∧ Nat.Prime p
```

`|- ∃ _, Nat.Prime _ ∧ _` returns 12 hits including `Nat.bertrand`;
`|- ∃ _, _ ∧ Nat.Prime _` returns 1, which is `Nat.exists_infinite_primes`.
Neither ordering alone is sufficient, so both are issued, and conjuncts with
no named anchor are blanked so `<` versus `≤` cannot hide a lemma.

After these three fixes the same goal was proved on the **first attempt**,
where five had previously failed. The prover did not improve; it was shown
the right lemma.

**Nothing in the module raises.** Bad query, malformed JSON, network down,
timeout: all return `[]`. Retrieval is an optimisation; without it the system
behaves as it did before retrieval existed.

---

## 9. What is trusted

| Trusted | Not trusted |
|---|---|
| SymPy | **the model** |
| Lean + Mathlib | model prose |
| the guard (~40 lines, unit-tested) | model self-assessment |
| Python and the parser allow-list | retrieved premises (checked by the compiler) |

The model is not in the trusted set. Swap a 3B model for a frontier one and
the soundness guarantee does not change — only coverage does. That was
measured: soundness was 97–99% at both 3B and 7B.

---

## 10. Design principles and where they came from

From the project design document:

| # | Principle | How it shows up |
|---|---|---|
| 1 | Separate reasoning from verification | the guard |
| 2 | Modular components | five packages, single responsibilities |
| 3 | Iterative reasoning | `reflection.py`, 13 recoveries |
| 4 | Hierarchical problem solving | decomposition, auxiliary lemmas |
| 5 | Explicit state | `AgentRun`, `ProofRun` |
| 6 | Framework independence | 2 LangChain imports |
| 7 | Incremental growth | violated once by a rewrite, then repaired |
| 8 | Extensibility | adding Lean was one registry line |

### Research lineage

| Paper | Idea | Where |
|---|---|---|
| **Prover Agent** (2506.19923) | informal proof as guidance | `Formalizer.sketch()` |
| | refinement on compiler errors | `PROOF_REFINEMENTS` |
| | auxiliary lemmas, bottom-up | `_gather_lemmas` |
| | refine the fewest-error draft | `best_draft()` |
| | synthesis from proved lemmas | `ProofStage.SYNTHESIS` |
| **AxProverBase** (2602.24273) | `sorry`/`admit` detection | `_uses_placeholder` |
| | `axiom` and suggestion tactics | `cheating_devices` |
| | library search | `retrieval/` |
| | reviewer for statement preservation | **not built** — needs a model |
| **LeanDojo** (2306.15626) | premise selection is the bottleneck | `retrieval/loogle.py` |
| | tactic-level goal states | **not built** |
| **miniF2F Revisited** (2511.03108) | separate formalisation from proving when measuring | methodology only |
| | LLM judges are unreliable (97.5% vs 62.7% human) | informs caution |
| **AI Co-Mathematician** (2605.06651) | hard programmatic constraints beat better prompting | the guard, anti-cheat checks |
| | bounded iteration prevents the "death spiral" | `MAX_ATTEMPTS`, never retry on FALSE |
| | reviewers can be optimised against | **design constraint, below** |

### A constraint on the reviewer agent, before it is built

The planned statement-preservation reviewer must be able to **lower** confidence
and never to **grant** it. It may downgrade a verdict to UNKNOWN. It must not
be able to produce a TRUE.

Two independent findings force this:

- *AI Co-Mathematician* (2605.06651): optimising against a reviewer can
  "converge to an argument that remains flawed, but where the errors can no
  longer be detected by the reviewer agent." A gate teaches the system to
  produce arguments that gate cannot catch.
- *miniF2F Revisited* (2511.03108): an LLM judge rated formalisations 97.5%
  correct where human experts found 62.7%.

A reviewer that can only refuse is safe under both. A reviewer that can approve
is a new way to be confidently wrong — which is the failure this entire
architecture exists to prevent.

---

## 11. Failures found, and how

Twelve real defects. **Code review found one of them.**

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
| 12 | Execution-flow steps 1–2 orphaned as unimportable dead code | design-doc audit |
| 13 | Retrieval searched identifiers, not the goal's conclusion | running it |
| 14 | Conjunction order hid the needed lemma from Loogle | running it |
| 15 | Six premises per query, ranked by module order, not relevance | running it |
| 16 | `in-mathlib` goal stated `n < p` where Mathlib gives `n ≤ p` | running it |
| 17 | Verification rates counted errored cases in the denominator | running it |
| 18 | The proving path had no rate-limit backoff | a failed run |
| 19 | Windows decoded Lean's UTF-8 output as cp1252 and crashed | a failed run |

Failures 3 and 8 are the same underlying problem and are **not solved**.

Failures 11 and 17 are the same shape as one previously fixed in
`variance.py` — three occurrences of: *a rate whose denominator includes
failures-to-run will eventually lie to you.* A Deep Agents run answered 8 of
8 correctly, was rate limited on the last two, and reported 80%.

Failures 13–15 are all retrieval, and 13 was **introduced by a fix**: the
conclusion-pattern change improved `IsCyclic` and silently broke every goal
concluding a quantifier. The lesson is not "test more" — it is that an
improvement verified on one example is a hypothesis, not a fix.

---

## 12. Known limitations

- **Statement preservation is unsolved.** The guard proves a check passed,
  not that it matched the question. Four mitigations, still open. The fix is
  back-translation, which needs a model trustworthy enough to judge.
- **Coverage.** SymPy cannot touch topology, group theory or set theory. Lean
  can, but needs a model that writes Mathlib.
- **Proving is demonstrated, not yet characterised.** Euclid's infinitude of
  primes has been proved twice, reproducibly, on the first attempt after the
  retrieval fixes. That is one theorem. The proof rate across a full tier is
  unmeasured, blocked on API quota rather than on anything in the code.
- **Statement preservation remains open.** The reviewer exists and is
  constrained to refuse only, but its own accuracy is unmeasured — and the
  literature says LLM judges overstate agreement by 30 points.
- **Deep Agents costs restraint.** 92% versus the LangChain harness, with one
  soundness failure. The specific tool call responsible has not been
  inspected.
- **Results are confounded.** Architecture and model size changed together.
- **Retrieval ranking is syntactic.** Loogle is not semantic search; LeanDojo's
  learned retriever would rank better.

---

## 13. Running it

```bash
pip install -r requirements.txt

export MRA_MODEL="openrouter:cohere/north-mini-code:free"
export OPENROUTER_API_KEY=...
export MRA_LEAN_PROJECT="$HOME/Projects/lean-workspace"   # optional

python scripts/ask.py "Is 561 a prime number?" --trace     # the agent
python scripts/run.py "Is 7919 prime?"                     # verify only
python scripts/prove.py "..." --dry-run                    # prove only
python scripts/search_mathlib.py "IsCyclic"                # premise search
python scripts/evaluate.py                                 # the 109 cases
pytest                                                     # 177 tests
```

`evaluate.py` exits non-zero on any soundness failure, so it works as a
regression gate.
