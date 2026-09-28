"""Which lemmas an accepted proof actually cites. Read from the record.

NO `from __future__ import annotations` (blueprint §5.1, gotcha 1).

WHY `finish` NEEDS THIS
-----------------------
A proof is only as readable as its citations. `by exact ha.add hb` is a
complete, compiled proof of "the sum of two even integers is even", and it
says nothing to a reader who does not already know that `Even.add` is Mathlib's
theorem `Even m → Even n → Even (m + n)`. The final answer should name each
lemma, say what it states and why it applies -- and it can only do that
honestly from something recorded, not from the model's memory of Mathlib.

Two things the run has already recorded supply that:

  * `premises` -- every lemma a search returned, WITH its type, module and
    docstring (`core/retrieval.search`, `proving.premises_for_error`).
  * `lemmas`  -- every helper this run proved itself with `try_lemma`, as the
    full declaration.

This module matches the accepted proof against both. It never calls Lean and
never asks the model; a name the proof cites that neither list knows is still
reported, marked as not looked up, so the reader can see it was used without
being told something about it that nothing verified.

WHAT IT CANNOT SEE
------------------
A tactic that picks lemmas itself (`simp`, `ring`, `omega`, `aesop`,
`norm_num`) cites nothing by name, so nothing is reported for it -- which is
the truth about that proof. Dot notation (`ha.add`) names only the last
component; it is matched to a searched premise ending in `.add` when exactly
one does, and reported as `via: dot notation`.
"""

import re

from math_v2.core import log

# A Lean identifier, possibly dotted: `Even.add`, `Int.emod_two_eq`,
# `mul_self_nonneg`, `ha.add`. Primes and `!`/`?` suffixes are legal.
_IDENT = re.compile(r"(?<![\w.'])[A-Za-z_][\w'!?]*(?:\.[A-Za-z_][\w'!?]*)*")

# `theorem foo (…) : … := …` / `lemma foo …` -- the name a kept lemma is cited by.
_DECLARED = re.compile(r"^\s*(?:private\s+)?(?:theorem|lemma)\s+([^\s(:{\[]+)", re.M)


# Projections a proof applies to a lemma rather than parts of its name:
# `Int.even_add.mpr` cites `Int.even_add`, `h.symm` cites `h`.
_PROJECTIONS = frozenset({"mp", "mpr", "symm", "trans", "elim", "out"})


def _strip_projections(token):
    while "." in token and token.rsplit(".", 1)[-1] in _PROJECTIONS:
        token = token.rsplit(".", 1)[0]
    return token


def _declared_name(declaration):
    match = _DECLARED.search(declaration or "")
    return match.group(1) if match else ""


def _premise_index(workdir):
    by_name = {}
    for entry in log.read(workdir).get("premises") or []:
        if isinstance(entry, dict) and entry.get("name"):
            by_name.setdefault(entry["name"], entry)
        elif isinstance(entry, str):
            by_name.setdefault(entry, {"name": entry})
    return by_name


def lemmas_used(workdir, proof):
    """The lemmas `proof` cites, in the order it first cites them.

    Each entry: `name`, `statement` (its Lean type, or "" when not looked
    up), `source` -- "mathlib" (a search returned it), "this run" (proved
    here with `try_lemma`) or "not looked up" (a dotted Mathlib-style name no
    search returned) -- plus `module`, `doc` and `via` ("name" or
    "dot notation").
    """
    if not proof:
        return []
    premises = _premise_index(workdir)
    kept = {}
    for declaration in log.kept_lemmas(workdir):
        name = _declared_name(declaration)
        if name:
            # The STATEMENT is the signature, not the declaration: a lemma the
            # tactic ladder filled carries a proof hundreds of lines long
            # (`first | rfl | ... `), and the reader needs what it says.
            kept.setdefault(name, _signature(declaration))

    by_suffix = {}
    for name in premises:
        by_suffix.setdefault(name.rsplit(".", 1)[-1], []).append(name)

    found, seen = [], set()

    def add(entry):
        if entry["name"] not in seen:
            seen.add(entry["name"])
            found.append(entry)

    for token in map(_strip_projections, _IDENT.findall(proof)):
        if token in kept:
            add({"name": token, "statement": kept[token], "source": "this run",
                 "module": "", "doc": "", "via": "name"})
        elif token in premises:
            p = premises[token]
            add({"name": token, "statement": p.get("type", "") or "",
                 "source": "mathlib", "module": p.get("module", "") or "",
                 "doc": p.get("doc", "") or "", "via": "name"})
        elif "." in token:
            head, _, last = token.rpartition(".")
            candidates = by_suffix.get(last, [])
            if head[:1].islower() and len(candidates) == 1:
                # `ha.add` where `ha : Even a` -- only when exactly one
                # searched premise could be meant.
                p = premises[candidates[0]]
                add({"name": p["name"], "statement": p.get("type", "") or "",
                     "source": "mathlib", "module": p.get("module", "") or "",
                     "doc": p.get("doc", "") or "", "via": "dot notation"})
            elif head[:1].isupper():
                # `Nat.succ_le_iff`, `Even.add`: a namespaced constant. Real
                # (it compiled) but its statement was never retrieved.
                add({"name": token, "statement": "", "source": "not looked up",
                     "module": "", "doc": "", "via": "name"})
    return [entry for entry in found if is_theorem(entry)]


# THEOREMS, NOT DEFINITIONS. MEASURED through Aura: a normalisation proof cited
# `Real.pi` and `Real.sin`, and both were listed as "lemmas used" -- a constant
# and a function, which the reader cannot be told "what they state".
#
# A recorded statement decides it: a definition's type ends in a TYPE (`ℝ`,
# `ℝ → ℝ`, `NNReal →*₀ NNReal`); a theorem's is a PROPOSITION (`Even m → Even n
# → Even (m + n)`, `Irrational √2`, `a = b`). Without a statement, Mathlib's
# naming convention decides: theorems are snake_case (`gcd_one_right`,
# `pi_ne_zero`); data definitions are single words or lowerCamelCase (`pi`,
# `sin`, `sqrt`, `gcd`).
_RELATION = re.compile(r"[=≠≤≥<>↔∣∈∉⊆⊂¬∀∃∧∨]|\bTrue\b|\bFalse\b")
_TYPE_HEADS = frozenset({
    "ℝ", "ℕ", "ℤ", "ℚ", "ℂ", "NNReal", "ℝ≥0", "ENNReal", "ℝ≥0∞", "EReal", "Prop",
    "Type", "Sort", "Bool", "Set", "Finset", "Multiset", "List", "Option", "Fin",
    "Matrix", "Polynomial", "Real", "Nat", "Int", "Rat", "Complex",
})
_ARROWS = re.compile(r"→[*+₀]*|≃[*+o]*|↪|⟶")


def _signature(declaration):
    """`theorem name (args) : type` -- a declaration without its proof."""
    return declaration.split(":=", 1)[0].strip()


def _codomain_head(statement):
    """The first token after the last top-level arrow, with binders removed.

    Loogle gives a signature as `binders : type` -- `Real.sin` is
    `(x : ℝ) : ℝ` -- so after the binders go, the type is what follows a
    leading `:`.
    """
    text = re.sub(r"[({\[][^(){}\[\]]*:[^(){}\[\]]*[)}\]]", " ", statement)   # (x : T) binders
    text = re.sub(r"^\s*:\s*", "", text)
    depth, last = 0, 0
    for i, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and _ARROWS.match(text, i):
            last = _ARROWS.match(text, i).end()
    tail = text[last:].strip()
    match = re.match(r"[^\s()]+", tail)
    return match.group(0) if match else ""


def is_theorem(entry):
    """True for a theorem or lemma; False for a definition or constant."""
    if entry.get("source") == "this run":
        return True                          # proved with `try_lemma`: a theorem
    statement = (entry.get("statement") or "").strip()
    if statement:
        if _RELATION.search(statement):
            return True
        return _codomain_head(statement) not in _TYPE_HEADS
    return "_" in entry.get("name", "").rsplit(".", 1)[-1]
