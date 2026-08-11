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

import config

# Ordered cheapest-and-most-specific first. Lean commits to the first that
# works, so a cheap tactic ahead of `aesop` avoids paying for the search.
STANDARD_TACTICS = (
    "rfl",
    "trivial",
    "assumption",
    "norm_num",
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

    for premise in list(premises)[:limit]:
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
