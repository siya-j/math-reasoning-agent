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
            kept.setdefault(name, declaration)

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
    return found
