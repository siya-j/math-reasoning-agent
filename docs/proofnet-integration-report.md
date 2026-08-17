# ProofNet integration — investigation report

**Investigation only. No code changed, no dataset added, nothing run.**

Everything below was checked against the live HuggingFace API and a real data
row, or against our own code. Claims are labelled where they are not.

---

## 1. Which ProofNet version

The original ProofNet (`hoskinson-center/proofnet`, 371 examples) is **Lean 3**
and unusable for us. Two maintained Lean 4 ports:

| Dataset | Rows | Splits | Columns | Notes |
|---|---|---|---|---|
| `UDACA/proofnet-lean4` | **371** | validation 185 / test 186 | 6 | faithful port; preserves the original split sizes exactly |
| `UDACA/proofnet-v3-lean4` | **365** | validation 182 / test 183 | 8 | based on v2, **entries causing Lean 4 syntax/parse errors removed**; adds `header_no_import` |

*(Fact — from `datasets-server.huggingface.co/size` for both.)*

**Recommendation: `UDACA/proofnet-v3-lean4`.** Six entries that cannot parse are
six guaranteed failures that tell us nothing about the agent, and the extra
`header_no_import` field is convenient. The cost is that our split sizes differ
slightly from published numbers, which must be stated whenever we quote a
figure.

### ProofNet# — and why it matters

*Reliable Evaluation and Benchmarks for Statement Autoformalization*
([arXiv 2406.07222](https://arxiv.org/abs/2406.07222), EMNLP 2025) reports that
the published Lean 4 ports contain **118 entries with formalisation mistakes —
31.8% of the total**. ProofNet# is their corrected release.

This is the single most important finding in this report. **Nearly a third of
the benchmark may be mis-formalised**, which means:

- A low proof rate on the uncorrected port is partly the benchmark's fault, not
  ours.
- Worse in the other direction: a *wrong* statement can be easier to prove than
  the intended one, so some successes would be spurious.

I could not locate a direct public download for ProofNet# in the searches I ran
— the paper and ACL Anthology page are available, the artefact link is not
obvious. **Action: find it before publishing any number.** Until then, every
ProofNet figure we produce carries a ±30% asterisk and should be described as
"on the uncorrected Lean 4 port".

---

## 2. Example structure

Verified against a real row from `proofnet/valid.jsonl`:

```json
{
  "name": "exercise_1_13a",
  "split": "valid",
  "informal_prefix": "/-- Suppose that $f$ is holomorphic in an open set $\\Omega$. Prove that if $\\text{Re}(f)$ is constant, then $f$ is constant.-/\n",
  "formal_statement": "theorem exercise_1_13a {f : ℂ → ℂ} (Ω : Set ℂ) (a b : Ω) (h : IsOpen Ω)\n  (hf : DifferentiableOn ℂ f Ω) (hc : ∃ (c : ℝ), ∀ z ∈ Ω, (f z).re = c) :\n  f a = f b :=",
  "goal": "f : ℂ → ℂ\nΩ : Set ℂ\n...\n⊢ f ↑a = f ↑b",
  "header": "import Mathlib\n\nopen Complex Filter Function Metric Finset\nopen scoped BigOperators Topology\n\n"
}
```

| Field | Contents | Our use |
|---|---|---|
| `name` | `exercise_1_13a` | → `Goal.id` |
| `split` | `valid` / `test` | selection |
| `informal_prefix` | the natural-language statement, wrapped in a Lean doc comment `/-- … -/`, with LaTeX | → `Goal.goal`, after stripping the delimiters |
| `formal_statement` | the Lean 4 theorem, **ending in `:=`** | see §4 |
| `goal` | the pretty-printed proof state | metadata only |
| `header` | `import Mathlib` **plus `open` declarations** | see §3, problem A |

**There is no proof field.** ProofNet ships statements, not proofs. Nothing to
compare our output against — the only judge is our own Lean compilation, which
is what our guard already does.

---

## 3. Compatibility problems

### A. Per-goal `open` declarations — real, and solvable with no change to `math_v2`

The `header` carries `open Complex Filter Function Metric Finset` and
`open scoped BigOperators Topology`. Without those, statements using `abs`,
`∑`, `𝓝` and similar will not elaborate. Our `build_source` uses a fixed
preamble:

```
verifiers/lean_verifier.py:39   DEFAULT_PREAMBLE = "import Mathlib\n"
math_v2/core/proving.py         5 call sites, all using the default
```

So a per-goal header cannot reach the compiler today.

**Solved without touching `math_v2`.** `rename_goal` renames the *last*
`theorem`/`lemma`; `open` lines are not declarations, so they pass through
untouched. Putting the header inside the statement text works — verified:

```lean
import Mathlib

open Complex Filter Function Metric Finset
open scoped BigOperators Topology

lemma helper : True := trivial          -- a kept auxiliary lemma still works

theorem mra_goal {f : ℂ → ℂ} … := by sorry
```

The adapter prepends the `open` lines (dropping the duplicate `import
Mathlib`) to `formal_statement`. Zero changes to the prover.

### B. ProofNet supplies the formal statement — the measurement decision

This is the point I have flagged repeatedly and it now has to be settled.
ProofNet gives the Lean statement, so it **bypasses our formalizer**. Two modes:

| | Mode A — informal only | Mode B — give the formal statement |
|---|---|---|
| Agent receives | `informal_prefix` | `formal_statement` |
| Measures | formalisation **and** proving | proving only |
| Comparable to | our own tiers | published ProofNet prover numbers |
| Formalisation rate | meaningful | not meaningful |
| Difficulty | much harder | the standard task |

**Recommendation: Mode B first.** It is what the published numbers measure, so
it is the only one that gives an external reference point, and it isolates the
prover — which is the half we have been improving. Mode A is a good second run
and will produce a much lower number; that is expected and honest, not a
regression.

Mode B needs no code change either: the adapter writes the goal text as
`Prove this Lean 4 theorem.\n\n<formal_statement>`. **Judgement call worth your
sign-off:** you said not to tune prompts for ProofNet. I read a fixed wrapper
applied identically to all 365 items as data preparation rather than prompt
tuning, but it is a line and you should agree it is on the right side.

### C. Mathlib version — cannot be checked from here

The ports were built in early 2025. Our `MRA_LEAN_PROJECT` pins whatever
`lake update` resolved when you built it. Mathlib renames aggressively — we
already hit `Basis` → `Module.Basis` on our own goals.

**I cannot check this from this machine.** Run on the Windows box:

```powershell
Get-Content "$env:MRA_LEAN_PROJECT\lean-toolchain"
Get-Content "$env:MRA_LEAN_PROJECT\lake-manifest.json" | Select-String -Pattern '"rev"|"name"' | Select-Object -First 10
```

*Inference:* some statements will fail to elaborate purely from version drift.
The v3 port removes parse errors but not semantic drift against a newer
Mathlib. The `check_statement` tool already separates that cleanly — a
`NOT_FORMALISED` outcome in Mode B means "this benchmark row does not compile
against our Mathlib", which is a benchmark-compatibility number worth reporting
separately from the proof rate.

### D. No held-out proofs

Nothing to diff against. Our guard is the only judge, which is the design — but
it means we cannot detect a proof that compiles a *different* theorem than
intended. The faithfulness lint helps only where numbers differ.

---

## 4. Can `math_v2` take ProofNet without a second prover?

**Yes.** No new prover, no change to reasoning. Three properties make it work:

1. `evaluate_proofs.py` dispatches on `config.PROVER` and knows nothing about
   provers — `MRA_PROVER=math_v2` already routes to `math_v2/harness.py`.
2. `eval/proof_dataset.load_goals(path=None)` **already accepts a path**, so a
   separate goals file needs no loader change.
3. The header problem is solved in data (§3A), and the formal statement is
   delivered as goal text (§3B).

---

## 5. Minimal files and changes

| File | Status | Lines | Purpose |
|---|---|---|---|
| `eval/proofnet.py` | **new** | ~70 | download the parquet/JSONL, convert to our `Goal` shape, write `eval/proofnet.json` |
| `eval/proofnet.json` | **new, generated** | — | 365 goals; `eval/proofs.json` untouched |
| `eval/proof_dataset.py` | edit | 1 | add `PROOFNET = "proofnet"` to `Tier` |
| `scripts/evaluate_proofs.py` | edit | 3 | `--goals PATH`, passed to `load_goals` |
| `tests/test_proofnet_adapter.py` | **new** | ~80 | schema, header handling, no-mutation of the existing dataset |

Nothing in `math_v2/` changes. The 7-goal near-mathlib benchmark is untouched
and still runs from `eval/proofs.json`.

### Two metric gaps to close first

You asked for nine measurements. Seven already flow end to end. Two do not:

| Metric | State |
|---|---|
| formalisation rate | ✓ `statement_ok` → `NOT_FORMALIZED` |
| proof rate | ✓ |
| model calls | ✓ (counted since `c0a9c93`) |
| Lean calls | ✓ |
| retrieval calls | ✓ |
| runtime | ✓ `telemetry.seconds` |
| failure reason | ✓ `detail` + `trace` |
| **symbolic/SymPy calls** | ✗ tracked by `budget` as `symbolic_calls`, **not carried into `Telemetry` or `ProofResult`** |
| **auxiliary lemmas** | ✗ `ProofResult.lemmas_total` exists but `harness._to_proof_run` never populates `run.lemmas`, so it is always 0 |

Both are reporting-layer gaps, ~10 lines total, and neither touches reasoning.
**They should be fixed before the smoke test, not after** — otherwise the first
ProofNet run cannot tell us whether the SymPy bridge or the lemma machinery
fired, which is exactly what we want to learn.

---

## 6. Proposed smoke test

15 problems from the **validation** split. Test stays held out so we can
iterate without contaminating the number we eventually report.

```powershell
cd C:\Users\SiyaJethliya\math-reasoning-agent
git pull
.\.venv\Scripts\python.exe -m pytest -q

# one-time: fetch and convert
.\.venv\Scripts\python.exe -m eval.proofnet --split validation --out eval\proofnet.json

$env:MRA_LEAN_PROJECT="C:\...\lean-workspace"
$env:MRA_EXEC="local"
$env:MRA_PROVER="math_v2"
$env:MRA_MAX_AGENT_SECONDS="300"
$env:MRA_MAX_AGENT_LEAN="8"
$env:MRA_MAX_AGENT_STEPS="20"
$env:MRA_MAX_AGENT_SEARCHES="8"

.\.venv\Scripts\python.exe scripts\evaluate_proofs.py `
    --goals eval\proofnet.json --limit 15 `
    --out eval\results\proofnet-smoke.json
```

Budget roughly 45–75 minutes for 15 problems. Expect a **much** lower rate than
7/7 — ProofNet is undergraduate analysis, algebra and topology, and 7/7 was on
goals we chose ourselves.

**What the smoke test is actually for**, in priority order:

1. Do the statements **elaborate** against our Mathlib? If a large fraction
   report `NOT_FORMALISED`, that is version drift (§3C), not the agent.
2. Does the `open`-header trick hold on real rows?
3. Do `try_lemma` / `try_skeleton` / the SymPy tools fire at all on genuinely
   hard problems? So far they never have.
4. Only then: the proof rate.

---

## 7. Summary of what I need from you

1. **Approve `UDACA/proofnet-v3-lean4`** — or say if you want the faithful 371.
2. **Approve Mode B** (give the formal statement) for the first run.
3. **Confirm the wrapper text is data prep, not prompt tuning** (§3B).
4. **Run the two Lean version commands** in §3C so I can assess drift.
5. **Agree to fix the two metric gaps first** (§5) — ~10 lines, no reasoning
   change.

Then I implement the adapter and run 15 problems, nothing more.

---

## Sources

- [zhangir-azerbayev/ProofNet](https://github.com/zhangir-azerbayev/ProofNet) — original benchmark
- [hoskinson-center/proofnet](https://huggingface.co/datasets/hoskinson-center/proofnet) — original Lean 3 release
- [UDACA/proofnet-lean4](https://huggingface.co/datasets/UDACA/proofnet-lean4) — faithful Lean 4 port
- [UDACA/proofnet-v3-lean4](https://huggingface.co/datasets/UDACA/proofnet-v3-lean4) — parse-clean port
- [Reliable Evaluation and Benchmarks for Statement Autoformalization](https://arxiv.org/abs/2406.07222) — ProofNet#, the 31.8% error finding
- [ACL Anthology 2025.emnlp-main.907](https://aclanthology.org/2025.emnlp-main.907/)
