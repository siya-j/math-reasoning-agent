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
    "norm_num",
    "decide",
    "simp",
    "positivity",
    "omega",
    "linarith",
    "aesop",
)

# Ways a retrieved premise might close a goal, in increasing desperation.
PREMISE_FORMS = (
    "exact {name}",
    "exact {name} _",
    "apply {name}",
    "simpa using {name}",
    "exact ⟨_, {name}⟩",
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
