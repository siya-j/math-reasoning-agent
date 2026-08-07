# Consolidation Phase — Report

Status of the Mathematical Scientific Reasoning Agent after the
architecture-hardening phase that followed Design Document Phases 1–5.

---

## 1. Why this phase existed

The roadmap said Phase 6 (Lean) was next. An audit against the design
document said otherwise:

- **Phases 4 and 5 had regressed.** The rewrite to a tool-calling agent
  deleted `pipeline/reflection.py`, `domain/attempt.py` and
  `domain/subclaim.py`. Retry and decomposition became *emergent behaviours*
  of the model rather than capabilities of the system. Measurement confirmed
  the concern: `mean checks per case` was **0.94**, meaning the agent
  frequently ran no iteration at all.
- **This contradicted the document's own methodology** — §8 Principle 7,
  *"Every stage extends the previous system"*, and *"Every new capability
  should extend the architecture instead of replacing it."* We had replaced.
- **The deepest known defect is one Lean would amplify.** With SymPy, a bad
  formalization usually yields a parse error. With Lean, it can yield a
  valid proof of the wrong theorem — which typechecks and looks rigorous.
  Prover Agent's own README warns of exactly this.

Decision: harden what exists before extending it.

---

## 2. Architecture: three versions, measured

### v1 — Fixed workflow (Phases 2–5)

The pipeline owned everything: interpret → classify → formalize → reason →
verify → explain. Deterministic and inspectable, but the LLM formalized in
a rigid schema and could not handle multi-part questions.

### v2 — Tool-calling agent

`create_agent` with verifiers as tools. The model chose which tools to call.
More flexible, and the **guard** kept the guarantee: the verdict was computed
from recorded tool results, never from model prose.

Cost: control flow moved inside the model. Phases 4 and 5 stopped happening.

### v3 — Hybrid (current)

```
question
  -> agent invocation          model picks tools and arguments
  -> guard                     verdict from records + faithfulness lint
  -> reflection, if needed     retry, bounded, decided in code    (Phase 4)
  -> decomposition, if still unverified   auxiliary evidence      (Phase 5)
  -> answer
```

The agent is a *node inside* the pipeline's flow rather than a replacement
for it. The model keeps genuine agency over **how** to verify; the pipeline
guarantees **that** iteration and decomposition happen.

---

## 3. Results

All runs qwen2.5:3b via Ollama unless noted.

| Architecture | Model | Cases | Accuracy | Soundness | Coverage |
|---|---|---|---|---|---|
| v1 workflow | qwen2.5:3b | 34 | 91% | 94% | 93% |
| v2 agent | qwen2.5:3b | 34 | 82% | 97% | 79% |
| v2 + docstring examples | qwen2.5:3b | 34 | 85% | 94% | 83% |
| v3 hybrid (limits slice) | qwen2.5:3b | 8 | 88% | 100% | 88% |
| **v3 hybrid (full)** | **qwen2.5:7b** | **87** | **95%** | **99%** | **95%** |

The final run also showed `restraint on abstract` = 100% (it verified nothing
on all twelve abstract claims) and **12 cases recovered by retry** — direct
evidence that the restored Phase 4 loop does real work rather than merely
existing.

**Confound, stated plainly:** the last row changes both the architecture
(v2 → v3) and the model (3B → 7B). The improvement cannot be attributed to
either alone. Separating them needs v3 on qwen2.5:3b over the same 87 cases.

Cross-model, same code:

| Model | Cases | Accuracy | Soundness |
|---|---|---|---|
| qwen2.5:3b (local) | 34 | 82% | 97% |
| gemini-3.5-flash-lite | 10 (easy slice) | 100% | 100% |

**Determinism.** Three repeats of eight cases: every verdict that completed
was identical every time. At temperature 0 the system appears deterministic,
so single-run figures are measurements rather than estimates.

**Metric definitions.** `missed` = a verdict existed but the agent could not
reach it (coverage gap, acceptable). `wrong` = asserted something untrue, or
claimed verification for something undecidable (soundness failure, must be
zero). The distinction is the point.

---

## 4. Failures found by evaluation

Every one of these was found by the golden dataset, not by code review.

| # | Failure | Root cause | Status |
|---|---|---|---|
| 1 | Constant of integration | `integrate(2x,x)` vs `x²+C` declared FALSE by instantiating unbound `C` | **fixed** — verifier refuses ill-posed identities |
| 2 | Invented symbol | Model produced a meaningless name; numeric checker ruled on it | **fixed** — numeric checks require actual numbers |
| 3 | Claim substitution | Asked "is 2 the *only* solution of x²=4?", the agent checked "are the solutions 2 and −2?" | **mitigated** — faithfulness lint, recorded claims, prompt rule |
| 4 | Docstring copying | A 3B model copied literal examples from tool docstrings verbatim into unrelated questions | **fixed** — no concrete literals in model-facing docstrings |
| 5 | Phase 4/5 regression | Retry and decomposition depended on model judgment | **fixed** — pipeline owns the outer loop |
| 6 | Metric misreport | Empty categories scored 0% and read as failure | **fixed** — reports `n/a` |
| 7 | Variance miscount | Errors counted as differing verdicts, so a rate-limited run looked non-deterministic | **fixed** — errors excluded from stability |
| 8 | Lowercase `i` | Asked for the roots of x²+1, the agent wrote `i`; SymPy parses that as an ordinary symbol, not the imaginary unit `I`, and the verifier declared a true claim FALSE | **fixed** — solutions naming symbols absent from the equation are refused |

### The one that matters most

Failure 3 is the deepest, because **every component behaved correctly and
the system was still wrong.** SymPy's answer was right. The guard's
aggregation was right. The agent simply verified a different claim than the
one asked.

The guard structurally cannot detect this: it sees that a check *passed*,
not which question the check was *for*. Mitigations added:

- every tool takes a `claim` argument, recorded and printed in the banner
- `pipeline/faithfulness.py` flags claimed solution sets containing numbers
  the question never mentions, downgrading the verdict to UNKNOWN
- a system-prompt rule forbidding silent correction of the user's claim

None of these is complete. A faithful-formalization guarantee remains open.

---

## 5. What the system can and cannot do

**Verifies** (deterministically, via SymPy): identities and derivatives and
integrals (`check_equality`), arithmetic (`check_numeric`), primality
(`check_primality`), equation solution sets (`solve_equation`), limits
including at infinity (`check_limit`).

**Refuses honestly**: abstract algebra, topology, functional analysis, set
theory, general proofs — reported as NOT VERIFIED rather than guessed. Also
refuses ill-posed checks (unbound constants, invented symbols).

**Cannot**: prove anything abstract (needs Lean); handle series, matrices or
inequalities (no tools yet); hold a conversation (single-turn); or guarantee
that the check it ran corresponds to the question you asked.

---

## 6. Design document compliance

| Principle | Status |
|---|---|
| 1 — Separate reasoning from verification | ✅ the guard enforces it structurally |
| 2 — Modular components | ✅ swapping the whole orchestration layer touched one package |
| 3 — Iterative reasoning | ✅ restored to code this phase |
| 4 — Hierarchical problem solving | ✅ restored to code this phase |
| 5 — Explicit state | ✅ `AgentRun` records every attempt and check |
| 6 — Framework independence | ✅ **two** LangChain imports in ~1,400 lines |
| 7 — Incremental growth | ⚠️ violated by the v2 rewrite; repaired by v3 |
| 8 — Extensibility | ✅ `Verifier` interface ready for Lean |

Success criteria (§13): six of seven met. The weak one is "maintains
explicit execution flow" — now substantially repaired, since the flow lives
in `pipeline/pipeline.py` rather than inside the model.

---

## 7. Next steps

### Needs no API key

1. **Full 87-case run on Ollama** — fills the missing row in §3. The only
   outstanding measurement of the current architecture.
2. **qwen2.5:7b comparison.** The machine has ~5.3 GiB of GPU memory and the
   3B model uses ~2 GiB, so 7B fits. This tests the model-capability
   hypothesis at zero cost — the single cheapest informative experiment
   available.
3. **More SymPy tools** — series, matrices, inequalities, factorization.
   Cheap. Caveat: more tools makes selection harder for small models, and
   the harness can now measure that trade.
4. **Lean verifier, offline half.** The `LeanVerifier` is deterministic
   infrastructure: subprocess call, compiler-error parsing, implementing the
   existing `Verifier` interface. It can be built and tested against
   hand-written Lean fixtures with **no model involved**. Only the
   formalization step needs a capable model. This decouples most of Phase 6
   from API access.

### Blocked on model access

5. **Multi-model sweep** — the same 87 cases across several models, to
   establish soundness as a function of model capability, holding
   architecture fixed.
6. **Lean end-to-end** — needs a strong model; Lean syntax is far harder to
   generate than SymPy syntax, and a 3B model will not produce compiling
   Mathlib proofs.
7. **Back-translation faithfulness check** — render the formal check back
   into English and compare with the original claim. Catches drift the
   numeric lint misses; needs a second capable model.

### Recommended order

(1) and (2) now — both free, and (2) may resolve the central open question
about whether the architecture's limits are architectural or model-driven.
Then (4), since it is the largest piece of Phase 6 and needs nothing but
engineering time. Keep (5)–(7) queued for whenever a key arrives.
