"""A deterministic proof attempt, costing one Lean compile and no model call.

WHY THIS EXISTS
---------------
Every proof outcome measured so far turned on RETRIEVAL, not proof search.
Once the right lemma is in hand, closing the goal is usually mechanical:

    theorem t (n : Nat) : ∃ p, n ≤ p ∧ Nat.Prime p := Nat.exists_infinite_primes n

No reasoning is required for that, so no model call should be spent on it.

ONE COMPILE, NOT SIXTY
----------------------
Lean's `first | t₁ | t₂ | ...` tries alternatives in order and commits to the
first that closes the goal. So thirty candidate tactics cost ONE invocation
(~20s) rather than thirty (~10 minutes). That is what makes trying the
obvious things affordable.

The candidates are: standard closers (`norm_num`, `simp`, `decide`, ...),
then `exact`/`apply`/`simpa using` against each retrieved premise.

A SECOND BENEFIT
----------------
This path needs no model beyond the one call that produced the statement. A
whole class of goals stays provable when the API is rate limited or down.

`exact?` and friends are deliberately NOT used: they report candidates rather
than committing to a proof, and verifiers/lean_runner.py correctly rejects
any proof containing them.
"""

from __future__ import annotations

import re

import config

# A declaration whose TYPE concludes `Prop`, `Type` or `Sort` is a definition,
# not a theorem: it names a predicate, and there is no proof in it to `exact`.
#
# MEASURED on proofnet `exercise_1_13c`. Retrieval returned `DifferentiableOn`
# — correctly, it is the most relevant declaration — and the ladder then spent
# a compile on nine forms of it:
#
#     exact DifferentiableOn | exact DifferentiableOn _ | apply DifferentiableOn
#
# None can ever work, because `DifferentiableOn 𝕜 f s : Prop` IS the goal's
# own predicate. The definition still belongs in the PROMPT, where it tells
# the model what the statement means; it does not belong in the ladder.
_DEFINITION = re.compile(r":\s*(Prop|Type|Sort)\b[^:]*$")


def provides_a_proof(premise) -> bool:
    """Could `exact <premise>` ever close a goal?

    Unknown types are kept. Loogle omits `type` on some hits, and dropping a
    premise for want of metadata would lose real lemmas to make the ladder
    tidier.
    """
    signature = (getattr(premise, "type", "") or "").strip()
    return not (signature and _DEFINITION.search(signature))

# Ordered cheapest-and-most-specific first. Lean commits to the first that
# works, so a cheap tactic ahead of `aesop` avoids paying for the search.
STANDARD_TACTICS = (
    "rfl",
    "trivial",
    "assumption",
    "norm_num",
    # A decision procedure for commutative (semi)ring equalities — cheap and
    # complete for exactly that shape, same class as norm_num just above.
    # MEASURED missing on `hard-sophie-germain`: the goal reduced to a pure
    # polynomial identity, the ladder had no `ring`, and two model-guided
    # compiles were spent hand-rolling `simp [add_mul, mul_add, ...]` trying
    # to reconstruct what `ring` closes in one shot — and failed both times.
    "ring",
    "decide",
    "simp",
    "simp_all",
    "positivity",
    "omega",
    "linarith",
    "aesop",
)

# Ways a retrieved premise might close a goal, in increasing desperation.
#
# The `assumption` variants exist because Mathlib lemmas usually take their
# hypotheses explicitly, and the goal's own context already holds them:
#
#   isCyclic_of_prime_card [Group α] [Fact (Nat.Prime p)] (h : Nat.card α = p)
#
# A bare `exact isCyclic_of_prime_card` cannot work — `h` must be supplied.
#
# The `Fact.mk` form handles INSTANCE arguments, which are a separate problem
# again: `Fact (Nat.Prime p)` must be in scope as an instance, not passed as
# an argument, so a `haveI` has to introduce it first.
PREMISE_FORMS = (
    "exact {name}",
    "exact {name} _",
    "apply {name}",
    "simpa using {name}",
    "exact ⟨_, {name}⟩",
    "exact {name} (by assumption)",
    "exact {name} ‹_›",
    "apply {name} <;> assumption",
    "(haveI := Fact.mk (by assumption); exact {name} (by assumption))",
)


def candidates(premises=(), limit: int | None = None) -> list[str]:
    """Every tactic worth trying without asking a model."""
    limit = config.CHEAP_PREMISES if limit is None else limit
    tactics = list(STANDARD_TACTICS)

    # Filtered BEFORE the limit, so the budget is spent on premises that could
    # actually close something rather than on the definitions that rank first.
    usable = [p for p in premises if provides_a_proof(p)]
    for premise in usable[:limit]:
        name = getattr(premise, "name", str(premise))
        if not name:
            continue
        tactics.extend(form.format(name=name) for form in PREMISE_FORMS)

    return tactics


def cheap_attempt(premises=(), limit: int | None = None) -> str:
    """A single `first | ...` proof body trying all of them at once."""
    tactics = candidates(premises, limit)
    if not tactics:
        return ""
    alternatives = "\n    | ".join(tactics)
    return f"by\n  first\n    | {alternatives}"
