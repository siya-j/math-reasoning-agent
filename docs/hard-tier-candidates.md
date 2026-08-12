# Hard-tier candidates — is this a reasoning agent or a retrieval system?

**Proposal only. No goals added, no code changed.**

Every Mathlib claim below was checked against the live Loogle service; the
query and its result are quoted, so a claim of "no one-shot lemma exists" is
evidence rather than an assumption.

---

## Why the current tier no longer discriminates

`math_v2` scores 7/7 on near-mathlib. The traces say what that measured:

| Goal | Attempts | What actually happened |
|---|---|---|
| `num-sqrt-two-irrational` | 1 | `exact irrational_sqrt_two` — the lemma is the answer |
| `lin-vector-space-basis` | 4 | 3 of the 4 failures were a **missing `by`** and a coercion |
| `num-primes-strictly-above` | 2 | one real step: apply the `≤` lemma at `n + 1` |

So the tier measures retrieval plus Lean syntax debugging. Only one goal
required a mathematical idea, and it was a single index shift. Nothing in the
set can distinguish "reasons about mathematics" from "finds the right name".

**A finding worth acting on separately:** the existing `novel-sum-of-two-squares`
is *not* novel.

```
?q="sq_add_sq", Nat.Prime
  Nat.Prime.sq_add_sq {p : ℕ} [Fact (Nat.Prime p)] (hp : p % 4 ≠ 3) : ∃ a b, a ^ 2 + b ^ 2 = p
  -- "Fermat's theorem on the sum of two squares"
```

It is one lemma plus a `Fact` instance and a `% 4 = 1 → % 4 ≠ 3` step. The
`novel` tier is currently mislabelled.

---

## The four capabilities, and which candidate tests each

| # | Capability | Primary candidate | Secondary |
|---|---|---|---|
| 1 | auxiliary lemmas / multi-step decomposition | **H1 AM-GM** | H3, H4 |
| 2 | SymPy discovery feeding a Lean proof | **H2 Sophie Germain** | H5, H3 |
| 3 | retrieval combining several library results | **H4 √2+√3** | H5 |
| 4 | direct strategy fails; needs goal-state feedback | **H1**, **H2** | H5 |

---

## H1 — `hard-amgm-sqrt`

**Informal.** For non-negative reals a and b, √(ab) ≤ (a+b)/2.

**Lean.**
```lean
theorem mra_goal (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) :
    Real.sqrt (a * b) ≤ (a + b) / 2
```

**Area.** Real analysis / inequalities.

**Capabilities.** 1 (decomposition), 4 (direct strategy fails), some 3.

**Is there a one-shot lemma?** No — checked.

```
?q=|- Real.sqrt (_ * _) ≤ _
  Found 46 declarations mentioning Real.sqrt, ≤, *.  Of these, 0 match.

?q="two_mul_le_add_sq"
  two_mul_le_add_sq (a b : R) : 2 * a * b ≤ a ^ 2 + b ^ 2
```

Mathlib has the **squared** AM-GM and not the square-root form. That gap is
exactly the reasoning step: the agent must get from `2xy ≤ x² + y²` to the
statement by instantiating at `x = √a`, `y = √b` and using `Real.sq_sqrt`.

**Why harder than the current 7.** `simp`, `norm_num`, `positivity` and
`nlinarith` all fail unenlightened. `nlinarith` succeeds only when *given* the
hint `sq_nonneg (Real.sqrt a - Real.sqrt b)` — the agent has to supply an
auxiliary term that appears nowhere in the goal. That is the first goal in the
set where reading a failed goal state is genuinely necessary.

**SymPy help.** Moderate. `check_inequality` on `(a+b)/2 - sqrt(a*b) ≥ 0`
confirms the claim before any compile, and `check_equality` on
`(sqrt(a)-sqrt(b))**2` vs `a - 2*sqrt(a*b) + b` surfaces the key identity.

**Natural auxiliary lemmas.** `Real.sqrt (a*b) = Real.sqrt a * Real.sqrt b`;
`0 ≤ (Real.sqrt a - Real.sqrt b)^2`; `Real.sq_sqrt ha`.

**Difficulty for Gemini 3.5 Flash.** Medium-hard. Well-known enough that the
idea should be recalled; the Lean assembly is the work.

**Within 300s / 8 compiles?** Yes — expect 3–6 compiles.

---

## H2 — `hard-sophie-germain`

**Informal.** For every natural n > 1, n⁴ + 4 is not prime.

**Lean.**
```lean
theorem mra_goal (n : ℕ) (hn : 1 < n) : ¬ Nat.Prime (n ^ 4 + 4)
```

**Area.** Number theory / algebraic identity.

**Capabilities.** 2 (SymPy discovery — the strongest case in the set),
1 (decomposition), 4 (no direct route).

**Is there a one-shot lemma?** No. Mathlib has no Sophie Germain identity and
no theorem about `n^4 + 4`. Retrieval cannot supply the idea, only the
machinery for "not prime".

**Why harder.** The proof turns entirely on a factorisation nobody will find
by searching:

```
n⁴ + 4 = (n² − 2n + 2)(n² + 2n + 2)
```

and then on showing **both factors exceed 1** — which is a second, separate
argument. This is the only candidate where the decisive step is a discovery
rather than a lookup.

**SymPy help.** Maximal, and it is the point of the goal. `factor(x**4 + 4)`
returns the identity immediately; `check_equality` verifies it; `check_numeric`
at n = 2, 3, 5 confirms compositeness before any Lean is written. This is the
cleanest test of "computation guides proof" we can construct with the nine
existing operations.

**Natural auxiliary lemmas.** The ring identity over ℕ or ℤ; `2 ≤ n² − 2n + 2`
for n > 1; then `Nat.Prime` refuted from a non-trivial divisor.

**Difficulty.** Hard — the hardest here. Two traps: **natural subtraction**
makes `n² − 2n + 2` treacherous on ℕ (a reason to consider stating it over ℤ,
or as `(n²+2)² − (2n)²`), and refuting `Nat.Prime` needs the right lemma shape.

**Within budget?** **At risk.** Plausibly 6–10 compiles. I would include it
knowing it may fail — a failure here is highly informative, because it isolates
"can the agent use a computed fact" from everything else.

---

## H3 — `hard-sum-odd-squares`

**Informal.** The sum of the first n odd numbers is n².

**Lean.**
```lean
theorem mra_goal (n : ℕ) : ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2
```

**Area.** Combinatorics / induction.

**Capabilities.** 1 (decomposition by induction), 2 (pattern discovery).

**Is there a one-shot lemma?** Not for this sum. Mathlib has Gauss's formula
only:

```
?q="sum_range_id"
  Finset.sum_range_id (n : ℕ) : ∑ i ∈ Finset.range n, i = n * (n - 1) / 2
  Finset.sum_range_id_mul_two (n : ℕ) : (∑ i ∈ Finset.range n, i) * 2 = n * (n - 1)
```

An agent could route through `sum_range_id` plus `Finset.sum_add_distrib`, or
prove it directly by induction. Two viable routes is a feature: it tests
whether the agent picks one and commits.

**Why harder.** It cannot be closed by citation. It needs either an induction
with `Finset.sum_range_succ` and a `ring` step, or a decomposition of the sum —
in both cases the agent constructs the argument.

**SymPy help.** Real but modest: `check_numeric` on n = 1…5 reveals 1, 4, 9,
16, 25 and hands the agent the closed form to prove.

**Natural auxiliary lemmas.** The successor step
`∑_{i<k+1} (2i+1) = ∑_{i<k} (2i+1) + (2k+1)`; `(k+1)² = k² + 2k + 1`.

**Difficulty.** Medium. **This is the control goal** — it *should* be solvable,
so a failure points squarely at the decomposition machinery rather than at the
mathematics being out of reach.

**Within budget?** Yes, comfortably. Expect 2–4 compiles.

---

## H4 — `hard-irrational-sqrt-sum`

**Informal.** √2 + √3 is irrational.

**Lean.**
```lean
theorem mra_goal : Irrational (Real.sqrt 2 + Real.sqrt 3)
```

**Area.** Number theory / field theory.

**Capabilities.** 3 (combining several library results — the strongest case),
1 (decomposition), 4.

**Is there a one-shot lemma?** No. Irrationality is **not** closed under
addition, so no lemma can take two irrationals to an irrational sum. Mathlib
supplies the pieces and not the conclusion — the search the agent already ran
on the easy version returned exactly the parts it would need:

```
irrational_sqrt_two
Nat.Prime.irrational_sqrt
irrational_sqrt_natCast_iff
```

**Why harder.** Direct contrast with `num-sqrt-two-irrational`, which was one
`exact`. Here the agent must square: if r = √2 + √3 were rational then
r² = 5 + 2√6, so √6 would be rational — contradicting irrationality of √6
(6 is not a perfect square). That is three linked steps and at least two
distinct library results, none of which mentions the goal.

**SymPy help.** Genuine: `check_equality` on `(sqrt(2)+sqrt(3))**2` against
`5 + 2*sqrt(6)` confirms the pivot before committing a compile.

**Natural auxiliary lemmas.** `Irrational (Real.sqrt 6)`; `(√2+√3)² = 5+2√6`;
closure of ℚ under the field operations.

**Difficulty.** Hard. The mathematics is standard, the Lean bookkeeping around
`Irrational` and rational closure is not.

**Within budget?** Borderline — 5–8 compiles. Include, expecting some failures.

---

## H5 — `hard-det-vanishes`

**Informal.** The 3×3 matrix with rows (1,2,3), (4,5,6), (7,8,9) is singular.

**Lean.**
```lean
theorem mra_goal : Matrix.det !![1, 2, 3; 4, 5, 6; 7, 8, 9] = (0 : ℤ)
```

**Area.** Linear algebra.

**Capabilities.** 2 (SymPy computes the determinant), 3 (retrieval of the
expansion plus the matrix-entry simp set).

**Is there a one-shot lemma?** Only an **expansion**, not an answer:

```
?q="det_fin_three"
  Matrix.det_fin_three (A : Matrix (Fin 3) (Fin 3) R) :
      A.det = A 0 0 * A 1 1 * A 2 2 - A 0 0 * A 1 2 * A 2 1 - ...
```

Nothing in Mathlib says this particular matrix is singular. The agent must find
the expansion, apply it, resolve `!![...]` entry notation, and finish with
arithmetic.

**Why harder.** No name gives the answer away. Its failure mode is *notation* —
which is precisely the situation where reading the goal state matters.

**SymPy help.** Direct: `check_matrix` / `check_numeric` establishes det = 0
before any compile, so the agent knows the target is reachable rather than
guessing.

**Natural auxiliary lemmas.** Few — this one is deliberately shallow.

**Difficulty.** Medium. Very likely within budget.

**Honest caveat.** This tests the SymPy→Lean bridge and Lean plumbing more than
mathematical reasoning. Worth including as a *bridge* probe; it should not be
read as evidence about reasoning either way.

**Within budget?** Yes. Expect 2–4 compiles.

---

## Summary

| id | area | capabilities | one-shot? | SymPy | difficulty | in budget |
|---|---|---|---|---|---|---|
| `hard-amgm-sqrt` | analysis | 1, 4 | **no** (verified) | moderate | med-hard | yes |
| `hard-sophie-germain` | number theory | 2, 1, 4 | **no** | **maximal** | hard | at risk |
| `hard-sum-odd-squares` | combinatorics | 1, 2 | **no** | modest | medium | yes |
| `hard-irrational-sqrt-sum` | number theory | 3, 1, 4 | **no** | genuine | hard | borderline |
| `hard-det-vanishes` | linear algebra | 2, 3 | expansion only | direct | medium | yes |

Five rather than six, deliberately: each one costs up to five minutes and real
API spend, and a tier where every goal fails teaches less than one with a
spread. H3 and H5 should be solvable, H1 is the interesting middle, H2 and H4
are meant to be at or past the edge.

## What the result would mean

- **All five proved.** The agent is doing more than retrieval. H2 in particular
  cannot be reached by searching.
- **H3 and H5 only.** It handles mechanical decomposition and computation but
  not the goals needing an idea — the retrieval-system hypothesis survives.
- **H2 fails with no `computation` records before the first compile.** The
  SymPy bridge is present but unused, and the prompt is the thing to fix.
- **H1 fails after repeated `try_proof` without `try_lemma` or `try_skeleton`.**
  Decomposition exists but is not being reached for — an agent-control finding,
  not a mathematical one.

That last pair is the reason to build this tier: each outcome names a specific
component rather than producing a single percentage.
