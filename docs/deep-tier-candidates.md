# Deep-tier candidates — does it reach for the right machinery, not just any machinery?

**Proposal, then built.** Every claim below was checked against the local
Mathlib source checkout (`.lake/packages/mathlib/Mathlib`), not assumed or
recalled — the exact file and theorem name is quoted for each, the same
discipline `hard-tier-candidates.md` used against live Loogle.

---

## A different question from `hard`

`hard` asks: does the agent find an idea Mathlib doesn't state directly?
Every `hard` goal is solvable by combining ordinary tactics and lemmas in a
way retrieval alone won't surface.

`deep` asks something narrower: is there exactly ONE kind of tool that can
possibly work, and does the agent recognise that and go get it? Each goal
here is a SIMPLE, famous statement whose standard proof is not available by
elementary means at all — not "harder that way," but genuinely unreachable
without a specific piece of advanced machinery (complex analysis, covering
space topology, a group-action argument, or L-functions). `omega`, `ring`,
`nlinarith`, induction, case-splitting — none of it applies. The only route
is citing the right named theorem.

**Deliberately excluded, and why this matters.** Several classical
"famous but hard" results were considered and dropped because they turned
out to have genuine elementary proofs, which would have tested the wrong
thing:

- Fermat's theorem that a prime `p ≡ 1 (mod 4)` is a sum of two squares —
  has an elementary infinite-descent proof; already correctly identified as
  *not* novel in `hard-tier-candidates.md` for the same reason.
- Bertrand's postulate — Erdős's proof is elementary (if clever).
- The infinitude of primes, unique factorisation — both elementary,
  already in `in-mathlib`.

A goal belongs here only if the standard proof is inherently advanced, not
merely if advanced tools happen to make it shorter.

**Also checked and dropped: Abel–Ruffini.** `Mathlib.FieldTheory.AbelRuffini`
exists, but its own module docstring says it proves only ONE DIRECTION —
"solvable by radicals ⟹ solvable Galois group" — not the headline negative
result (a specific quintic is *not* solvable). Citing this file cannot close
a "the general quintic has no radical formula" goal; the agent would have to
assemble a real Galois-theory argument (show a concrete polynomial's Galois
group is `S_5`, show `S_5` is not solvable, chain through the direction that
exists) from scratch. That tests original mathematical construction, which
is a different capability than this tier is built to probe.

**Also checked and absent from this Mathlib entirely:** Brouwer's fixed
point theorem, Picard's little theorem, the Prime Number Theorem. Good
fits for the criterion, not usable here regardless.

---

## D1 — `deep-fta`

**Informal.** Every non-constant polynomial with complex coefficients has a
root in the complex numbers.

**Area.** Complex analysis.

**Mathlib.** `Complex.exists_root {f : ℂ[X]} (hf : 0 < degree f) : ∃ z, IsRoot f z`
— confirmed in `Mathlib/Analysis/Complex/Polynomial/Basic.lean`. The file's
own docstring: "proves that every nonconstant complex polynomial has a root
using Liouville's theorem."

**Why necessarily advanced.** No purely algebraic or real-analytic proof
exists. Every known proof route needs complex analysis (this one — a
polynomial with no root has an entire, bounded reciprocal, so Liouville
forces it constant), a topological winding-number argument, or Galois
theory. The statement is elementary; the proof cannot be.

**Difficulty for this agent.** Should be a clean one-shot citation if
retrieval surfaces `Complex.exists_root` (or the `IsAlgClosed ℂ` instance) —
the goal's whole point is testing WHETHER it does, not whether it can
reconstruct Liouville's theorem itself.

---

## D2 — `deep-liouville`

**Informal.** A function from the complex numbers to the complex numbers
that is complex differentiable everywhere and whose values are bounded must
be constant.

**Area.** Complex analysis.

**Mathlib.** `Differentiable.exists_eq_const_of_bounded {f : E → F}
(hf : Differentiable ℂ f) (hb : IsBounded (Set.range f)) : ∃ c, f = Function.const E c`
— confirmed in `Mathlib/Analysis/Complex/Liouville.lean`, docstring "**Liouville's theorem**".

**Why necessarily advanced.** Inherently complex-analytic: the proof bounds
all derivatives of `f` from a single bound on `f` itself, via Cauchy's
integral formula — a fact with no real-analysis analogue. The real-variable
version of the statement is false (`sin x` is real differentiable
everywhere and bounded, and not constant), so no elementary substitute can
exist; complex differentiability is doing real work here, not decoration.

**Difficulty for this agent.** A citation goal like D1, with one added
translation step the formalizer must get right: "complex differentiable
everywhere" is `Differentiable ℂ f`, not `Differentiable ℝ f` — getting
this wrong produces a FALSE, unrelated statement rather than a proof
failure, which is itself worth observing.

---

## D3 — `deep-nielsen-schreier`

**Informal.** Every subgroup of a free group is itself a free group.

**Area.** Group theory.

**Mathlib.** `subgroupIsFreeOfIsFree {G : Type u} [Group G] [IsFreeGroup G]
(H : Subgroup G) : IsFreeGroup H` — confirmed in
`Mathlib/GroupTheory/FreeGroup/NielsenSchreier.lean`, docstring "The
Nielsen-Schreier theorem: a subgroup of a free group is free."

**Why necessarily advanced.** The classical proofs go through covering
space theory (a subgroup corresponds to a covering space of a wedge of
circles, and every covering space of a graph is a graph, hence has free
fundamental group) or the combinatorial Reidemeister–Schreier rewriting
method. No proof from the group axioms alone, without either topological or
combinatorial machinery built for this purpose, is known — a genuinely
surprising fact for a statement this short.

**Difficulty for this agent, honestly.** The hardest citation target in
this tier. `subgroupIsFreeOfIsFree` is an `instance`, not a plain theorem,
and the surrounding file is built on `IsFreeGroupoid`/`Groupoid`
infrastructure the formalizer's statement will not obviously connect to.
Expect this to test retrieval and Lean-idiom translation (typeclass
resolution via `IsFreeGroup`) as much as the mathematical recognition —
worth keeping for that reason, not despite it.

---

## D4 — `deep-sylow`

**Informal.** If `p` is a prime number and `p^n` divides the order of a
finite group `G`, then `G` has a subgroup of order `p^n`.

**Area.** Group theory.

**Mathlib.** `Sylow.exists_subgroup_card_pow_prime [Finite G] (p : ℕ)
{n : ℕ} [Fact p.Prime] (hdvd : p ^ n ∣ Nat.card G) : ∃ K : Subgroup G, Nat.card K = p ^ n`
— confirmed in `Mathlib/GroupTheory/Sylow.lean`.

**Why included, and the honest caveat.** Flagged deliberately as the
borderline pick. Its proof needs a real idea — a group action on the set of
`p^n`-element subsets, or an inductive normalizer argument — not reducible
to counting or induction on the group's order alone. But that idea is
"clever," in the sense every textbook group theory course teaches, rather
than requiring machinery imported from an entirely different field
(analysis, topology). Included because it is the canonical textbook example
of a simple-to-state finite group theory result resisting brute force; cut
this one first if the tier needs trimming toward stricter "outside
machinery" cases only.

**Difficulty for this agent.** A clean one-shot citation if found — the
binder shape (`Fact p.Prime`, `Nat.card` rather than `Fintype.card`) is the
likely source of friction, not the mathematics.

---

## D5 — `deep-dirichlet-ap`

**Informal.** If `a` and `n` are coprime natural numbers, there are
infinitely many prime numbers congruent to `a` modulo `n`.

**Area.** Number theory.

**Mathlib.** `Nat.forall_exists_prime_gt_and_modEq (m : ℕ) {q a : ℕ}
(hq : q ≠ 0) (h : a.Coprime q) : ∃ p > m, p.Prime ∧ p ≡ a [MOD q]` —
confirmed in `Mathlib/NumberTheory/LSeries/PrimesInAP.lean`, docstring
"**Dirichlet's Theorem** on primes in arithmetic progression."
(`Nat.infinite_setOfPred_prime_and_modEq`, same file, states the
`Set.Infinite` form directly and may formalize more cleanly.)

**Why necessarily advanced.** The general theorem's only known proofs go
through Dirichlet L-functions — showing `L(1, χ) ≠ 0` for every nontrivial
character `χ` mod `n` — which is genuinely complex-analytic. Unlike some
special cases (`a = 1` reduces to properties of cyclotomic polynomials and
has an elementary proof), no elementary argument covers the general
coprime `a`.

**Difficulty for this agent, honestly.** The deepest citation target in the
set. The implementing file runs to hundreds of lines of L-function
machinery culminating in this one usable theorem — finding and correctly
applying `Nat.forall_exists_prime_gt_and_modEq` by name, buried behind that
much supporting infrastructure, is itself a real test of retrieval quality,
independent of whether the model could ever reconstruct the analytic
argument.

---

## Summary

| id | area | mathlib citation | why advanced | citation depth |
|---|---|---|---|---|
| `deep-fta` | complex analysis | `Complex.exists_root` | no algebraic/real-analytic proof exists | shallow |
| `deep-liouville` | complex analysis | `Differentiable.exists_eq_const_of_bounded` | real-variable analogue is FALSE | shallow |
| `deep-nielsen-schreier` | group theory | `subgroupIsFreeOfIsFree` | needs covering-space topology or Reidemeister–Schreier | deep, awkward Lean shape |
| `deep-sylow` | group theory | `Sylow.exists_subgroup_card_pow_prime` | needs a real group-action idea (borderline: "clever," not "outside machinery") | shallow |
| `deep-dirichlet-ap` | number theory | `Nat.forall_exists_prime_gt_and_modEq` | general case only provable via L-functions | very deep |

## What the result would mean

- **D1, D2, D4 proved; D3, D5 not.** Expected split. The agent can find and
  cite a well-known named theorem when retrieval surfaces it cleanly, but
  struggles once the citation is buried behind unfamiliar Lean
  infrastructure (typeclasses, groupoids) or behind hundreds of lines of
  supporting machinery it has to search through.
- **All five proved.** Retrieval and citation-recognition are genuinely
  strong — worth then testing whether the agent can go one step further and
  combine two deep citations, not just find one.
- **All five fail, especially D1/D2.** Worth checking first whether the
  formalizer is stating them correctly (see D2's real-vs-complex
  differentiability trap) before concluding anything about retrieval or
  reasoning.
- **Failures cite the RIGHT name but the wrong signature** (e.g. mismatched
  binder order, an `instance` cited as if it were a plain `theorem`) is a
  distinct, more encouraging failure mode than searching and finding
  nothing — it means retrieval worked and only the Lean mechanics didn't.
