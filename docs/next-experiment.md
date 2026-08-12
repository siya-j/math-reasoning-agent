# Next experiment — runbook

Written to be run on the Windows machine with no assistant available.
Copy-paste each block in order. Every step says what to look for.

**The question this answers:** does the agentic architecture actually beat the
pipeline, once the three bugs that were crippling *both* are fixed?

---

## Why this is not just "run the baseline"

The 57% → 86% jump on near-mathlib was **not** an architecture result. Both
runs were `MRA_PROVER=agentic`; what changed was three bug fixes:

| Bug | Lives in | Used by |
|---|---|---|
| 20 — retrieval discarded Loogle's corrections | `retrieval/loogle.py` | **both provers** |
| 21 — the goal name shadowed the lemma proving it | `verifiers/lean_verifier.py` | **both provers** |
| 22 — unelaborable statement scored as a proving failure | `pipeline/statement.py` | agentic only |

Bugs 20 and 21 were hurting the pipeline prover exactly as much. It has never
been measured with them fixed. Expect it to improve too — possibly a lot.

**And bug 22's fix is asymmetric.** Only the agentic prover pre-flights and
repairs its statement, because `prover.py` is deliberately frozen. So a
straight `pipeline` vs `agentic` run compares two things at once. Arm B below
switches it off with `MRA_CHECK_STATEMENT=0` so exactly one variable moves.

---

## Step 0 — sync and verify

```powershell
cd C:\Users\SiyaJethliya\math-reasoning-agent
git pull
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:MRA_PROVER -ErrorAction SilentlyContinue
Remove-Item Env:MRA_CHECK_STATEMENT -ErrorAction SilentlyContinue
```

**Expect:** `311 passed, 1 skipped`. If not, stop and report — nothing below
is worth running against a broken tree.

---

## Step 1 — ARM A: the pipeline baseline (the important one)

```powershell
$env:MRA_PROVER="pipeline"
$env:MRA_CHECK_STATEMENT="0"
.\.venv\Scripts\python.exe scripts\evaluate_proofs.py `
    --tier near-mathlib --depth 0 `
    --out eval\results\near-A-pipeline.json
```

**Expect:** slow. The baseline has no step or time budget — those bound the
agentic prover only — and one goal took 360s before. Budget ~40 minutes.

**Look for:** the proof rate, and how many goals produce byte-identical
attempts (visible later in the trace). The old 57%-era claim that "pipeline
doesn't work" rests on a single goal measured before all three fixes.

---

## Step 2 — ARM B: agentic, same conditions

```powershell
$env:MRA_PROVER="agentic"
$env:MRA_CHECK_STATEMENT="0"
.\.venv\Scripts\python.exe scripts\evaluate_proofs.py `
    --tier near-mathlib --depth 0 `
    --out eval\results\near-B-agentic-nocheck.json
```

**A vs B is the architecture comparison.** Same model, same goals, same depth,
same bug fixes, statement repair off on both. This is the number that belongs
in the writeup.

---

## Step 3 — ARM C: agentic with statement repair

```powershell
$env:MRA_PROVER="agentic"
Remove-Item Env:MRA_CHECK_STATEMENT -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe scripts\evaluate_proofs.py `
    --tier near-mathlib --depth 0 `
    --out eval\results\near-C-agentic-check.json
```

**B vs C isolates what statement repair is worth.** The earlier 86% is
roughly this arm, but it predates the repair loop becoming multi-round with
memory, so it needs re-running to be quotable.

**Note:** C's *proof rate* is not comparable to the old 57%. Unelaborable
statements now leave the proof-rate denominator and land in the formalisation
rate instead. Compare `formalisation rate` and `proof rate | formalised`
separately — that split is the miniF2F Revisited methodology and it is the
honest way to report this.

---

## Step 4 — the one remaining failure

```powershell
$env:MRA_PROVER="agentic"
Remove-Item Env:MRA_CHECK_STATEMENT -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe scripts\evaluate_proofs.py `
    --goal lin-vector-space-basis `
    --out eval\results\basis.json
.\.venv\Scripts\python.exe scripts\show_proof_run.py `
    --file eval\results\basis.json --goal lin-vector-space-basis --full
```

**Hypothesis being tested.** The statement has *two* independent faults:

```lean
theorem exists_basis (K : Type u) (V : Type v) [DivisionRing K]
    [AddCommGroup V] [Module K V] : ∃ (ι : Type v), Nonempty (Basis ι K V)
```

1. `Basis` was renamed; current Mathlib has `Module.Basis`. **Verified**
   against the live Loogle service.
2. `Type u` and `Type v` are used with no `universe u v` declaration.
   **Unverified inference** — it needs Lean to confirm.

Lean reports only what stopped it, so one repair round could never fix both.
The loop is now multi-round with memory (each attempt sees every rejected
version and its error), which is why this is worth re-running.

**Look for** these trace lines, in order:

```
statement rejected      <- fault 1
repair 1 rejected       <- fault 1 fixed, fault 2 now visible
statement repaired      <- both fixed
```

If instead you see `repair repeated itself`, the model is stuck and the fix
is a better repair prompt, not more rounds. **Bring the full trace back
either way.**

---

## Step 5 — re-run in-mathlib

```powershell
$env:MRA_PROVER="agentic"
.\.venv\Scripts\python.exe scripts\evaluate_proofs.py `
    --tier in-mathlib --depth 0 `
    --out eval\results\in-agentic.json
```

Bug 21 hit this tier hardest: a goal already in Mathlib is the one most likely
to be named after the lemma that proves it. The old 5/6 is not a clean number.

Also settles the outstanding `alg-square-nonneg` unknown.

---

## Step 6 — bring back

```powershell
Get-ChildItem eval\results\*.json | Select-Object Name, Length
```

Copy back:

- the four summary blocks printed at the end of each run
- `eval\results\*.json`
- the full trace from Step 4

---

## What NOT to run yet

- **`--depth 1` with `MRA_PROVER=agentic`.** It does nothing. The agentic
  prover ignores `depth` (`**_ignored`); only the baseline's `_gather_lemmas`
  reads it. Running it would produce a meaningless "decomposition doesn't
  help" result. Test depth on the **pipeline** prover, or wire decomposition
  into the agentic one first.
- **`--tier novel`.** Two goals, both genuinely hard. Worth doing, but after
  the architecture comparison, not instead of it.
- **ProofNet.** It supplies the formal statement and so bypasses the
  formalizer — it measures a different system than this one. The formalizer
  is currently the binding constraint, which is exactly what ProofNet cannot
  see.

---

## Recording the result

Fill this in as the runs finish. The three-way split matters more than any
single headline number.

| Arm | Prover | Statement repair | Formalised | Proved \| formalised | Proof rate |
|---|---|---|---|---|---|
| A | pipeline | off | | | |
| B | agentic | off | | | |
| C | agentic | on | | | |

- **A vs B** = what the architecture is worth.
- **B vs C** = what statement repair is worth.
- Neither is `57% -> 86%`, which was three bug fixes on one architecture.
