"""Which of a theorem's binders a subgoal actually needs.

NO `from __future__ import annotations` (blueprint §5.1, gotcha 1).

THE FAILURE THIS EXISTS FOR
---------------------------
Automatic lemma synthesis compiled each hole as a STANDALONE theorem:

    theorem mra_lemma_1 : ∀ x ∈ Ω, deriv f x = 0

`Ω` and `f` are binders of the parent theorem. Outside it they do not exist, so
every hole that mentions the goal's own objects failed to elaborate — which is
nearly every hole on a real analysis goal. Decomposition therefore fired and
achieved nothing, and the limitation was invisible because the unit tests used
holes over globals.

WHAT THIS DOES
--------------
Copies the binders the claim NEEDS, and only those:

    theorem ex {f : ℂ → ℂ} (Ω : Set ℂ) (a b : Ω) (h : IsOpen Ω)
      (hf : DifferentiableOn ℂ f Ω) : f a = f b

    claim `∀ x ∈ Ω, deriv f x = 0`
      mentions Ω, f
      Ω needs nothing; f needs nothing
      ->  theorem mra_lemma_1 {f : ℂ → ℂ} (Ω : Set ℂ) : ∀ x ∈ Ω, deriv f x = 0

Copying EVERY binder would be simpler and is wrong: it drags in hypotheses the
claim does not use, so the lemma states something weaker than it could, and a
hypothesis that is itself unprovable would make the lemma unprovable too.

THE CLOSURE IS THE PART THAT MATTERS. `(a b : Ω)` cannot be copied without
`(Ω : Set ℂ)`, so a binder's TYPE pulls in the binders it mentions, transitively,
and the original declaration order is preserved because Lean requires it.

Everything here is string manipulation over a signature. No model, no compiler.
"""

import re

# One binder group: `(a b : Ω)`, `{f : ℂ → ℂ}`, `[Group G]`, `⦃x : α⦄`.
_OPENERS = {"(": ")", "{": "}", "[": "]", "⦃": "⦄"}

# A Lean identifier. Deliberately permissive — subscripts and primes are common
# in Mathlib-style names (`F₁`, `hf'`).
_IDENT = re.compile(r"[A-Za-z_ℂℝℕℤΩα-ωΑ-Ω][\w'₀-₉α-ωΑ-Ω]*")


def split_binders(text):
    """Every binder group in a signature, in order.

    Returns a list of dicts: {raw, names, type, kind}. `names` is empty for an
    instance binder like `[Group G]`, which declares no name to depend on.
    """
    groups = []
    index = 0
    text = text or ""
    while index < len(text):
        char = text[index]
        if char not in _OPENERS:
            index += 1
            continue

        close = _OPENERS[char]
        depth = 0
        end = index
        while end < len(text):
            if text[end] in _OPENERS:
                depth += 1
            elif text[end] in _OPENERS.values():
                depth -= 1
                if depth == 0:
                    break
            end += 1
        if end >= len(text):
            break

        raw = text[index:end + 1]
        inner = raw[1:-1]
        head, sep, tail = inner.partition(":")
        # `:=` inside a binder is a default value, not an ascription.
        if sep and not tail.startswith("="):
            names = head.split()
            type_text = tail.strip()
        else:
            names, type_text = [], inner.strip()
        groups.append({"raw": raw, "names": names, "type": type_text,
                       "kind": char})
        index = end + 1
    return groups


def identifiers(text):
    return set(_IDENT.findall(text or ""))


def needed_binders(binders, claim):
    """The binders a claim depends on, transitively, in declaration order.

    Instance binders (`[Group G]`) are pulled in whenever any type they mention
    is already needed: they declare no name of their own, so nothing can refer
    to them explicitly, and omitting one leaves the lemma unelaborable in
    exactly the cases where it mattered.
    """
    wanted = identifiers(claim)
    keep = set()

    changed = True
    while changed:
        changed = False
        for position, binder in enumerate(binders):
            if position in keep:
                continue
            named = set(binder["names"])
            if named & wanted:
                hit = True
            elif not named:
                # An instance binder: needed once its subject is.
                hit = bool(identifiers(binder["type"]) & wanted)
            else:
                hit = False
            if hit:
                keep.add(position)
                wanted |= identifiers(binder["type"]) | named
                changed = True

    return [binders[i]["raw"] for i in sorted(keep)]


def with_hypotheses(binders, keep_raw):
    """Add the hypotheses that talk ONLY about what is already in scope.

    A claim references the objects it is about (`Ω`, `f`) but not the
    hypotheses needed to prove it: nothing in `∀ x ∈ Ω, deriv f x = 0` mentions
    `hf : DifferentiableOn ℂ f Ω`, and without `hf` the lemma is not provable.
    So a second pass adds any binder whose type mentions no name outside the
    scope already fixed.

    This is what stops it becoming "copy everything". If the claim needs no
    binders, the scope is empty, no hypothesis qualifies, and the lemma is
    standalone — which is right for a hole like `1 = 1`.
    """
    if not keep_raw:
        return []

    by_raw = {b["raw"]: b for b in binders}
    kept = [by_raw[r] for r in keep_raw if r in by_raw]
    in_scope = {name for b in kept for name in b["names"]}
    for b in kept:
        in_scope |= identifiers(b["type"])

    declared = {name for b in binders for name in b["names"]}
    out = []
    for b in binders:
        if b["raw"] in keep_raw:
            out.append(b["raw"])
            continue
        # A hypothesis qualifies when every DECLARED name it mentions is
        # already in scope. Globals (`Set`, `IsOpen`, `ℂ`) are not declared
        # here and never disqualify anything.
        # `(a b : Ω)` is DATA over an in-scope type, not a hypothesis about
        # it. Copying it universally quantifies the lemma over points nothing
        # needs. A type that is exactly a declared name is the one case this
        # can be told apart from a real hypothesis without parsing Lean.
        if b["type"].strip() in declared:
            continue
        mentioned = identifiers(b["type"]) & declared
        if mentioned and mentioned <= in_scope:
            out.append(b["raw"])
    return out


def lemma_signature(name, statement_binders, claim):
    """`theorem <name> <needed binders> : <claim>`, or "" if nothing fits."""
    binders = split_binders(statement_binders)
    needed = with_hypotheses(binders, needed_binders(binders, claim))
    prefix = (" " + " ".join(needed)) if needed else ""
    return f"theorem {name}{prefix} : {claim}"
