"""Proof skeletons: decomposition WITHIN a single proof.

Auxiliary lemmas (Prover Agent §3.2) decompose a problem across separate
theorems. This decomposes one theorem into steps:

    theorem t ... := by
      have h1 : <intermediate claim> := by sorry
      have h2 : <intermediate claim> := by sorry
      exact foo h1 h2

WHY THIS IS THE IMPORTANT SIGNAL
--------------------------------
A skeleton that compiles WITH `sorry` has proved something real: the
decomposition typechecks. Every `have` is well formed, the final step follows
from them, and all that remains is a set of smaller, INDEPENDENT subgoals.

That converts one hard problem into several easy ones — and each hole can be
attacked by the deterministic tactic ladder first, costing no model call.

Nobody writes a twenty-line Mathlib proof in one shot. Asking for the whole
thing five times is the wrong shape for the task.

Everything here is string manipulation over Lean source. No model, no
compiler, so all of it is testable offline.
"""

from __future__ import annotations

import re

# `sorry` as a standalone token. A theorem named `sorryless` is not a hole.
_HOLE = re.compile(r"\bsorry\b")

# `have <name> : <claim> := by sorry` — the claim is what a filler must prove.
#
# `:(?!=)` IS LOAD-BEARING. Without it, `have h := f x` matches with the `:` of
# `:=` read as the ascription colon, and the "claim" becomes everything up to
# the NEXT `:=`. Measured on the near-mathlib goal `lin-vector-space-basis`,
# whose skeleton was
#
#     have h := Module.Basis.exists_basis K V
#     rcases h with ⟨s, hs⟩
#     have b := hs.some
#     sorry
#
# and which yielded the claim
#
#     '= Module.Basis.exists_basis K V\n  rcases h with ⟨s, hs⟩\n  have b'
#
# An unascribed `have` states no claim, so it must produce "" — the same answer
# as a `sorry` that is not a `have` at all.
_HAVE = re.compile(r"have\s+(\w+)\s*:(?!=)\s*(.+?)\s*:=", re.DOTALL)


def hole_count(proof: str) -> int:
    """How many `sorry` placeholders remain."""
    return len(_HOLE.findall(proof or ""))


def fill_hole(proof: str, index: int, replacement: str) -> str:
    """Replace the `index`-th `sorry` (0-based) with a tactic.

    Only one hole changes, so a failed fill can be reverted without
    disturbing holes that already succeeded.

    EVERY LINE PAST THE FIRST is re-indented to the HOLE's own column, not
    left at whatever column `replacement` was written at. This is the exact
    bug `verifiers.lean_verifier.declaration()` had and was fixed for: `sorry`
    sits at some column inside the skeleton (typically deep inside a `have`
    line), and Lean's tactic blocks are indentation-significant, so a
    multi-line filler landing at column 0 is SHALLOWER than the skeleton
    around it and silently ends the enclosing block right there — reported
    back as "unsolved goals" naming the untouched original claim, no
    different from the filler's own tactics being wrong. A single-line
    replacement (the common case: `exact foo`, one `first | ...` alternative
    written on one line) is returned unchanged; only a real multi-line
    filler needs this at all.
    """
    seen = -1

    def swap(match: re.Match) -> str:
        nonlocal seen
        seen += 1
        if seen != index:
            return match.group(0)
        lines = replacement.splitlines()
        if len(lines) <= 1:
            return replacement
        column = match.start() - proof.rfind("\n", 0, match.start()) - 1
        pad = " " * column
        return "\n".join(
            [lines[0]] + [f"{pad}{line}" if line.strip() else line
                         for line in lines[1:]]
        )

    return _HOLE.sub(swap, proof or "")


_LEADING_BY = re.compile(r"^\s*by\b\s*", re.DOTALL)


def bare_tactic(text: str) -> str:
    """Strip a caller's own leading `by`, if it has one.

    A hole is always `sorry` sitting right after an existing `:= by` in the
    skeleton — a filler supplies what comes AFTER that `by`, never another
    one. MEASURED: `cheap_attempt()` returns a whole standalone proof body,
    `by` included, because that is what its OTHER callers need; used
    directly as a hole-filler it produced `:= by by\n  first\n    | ...`,
    which is not valid Lean (`by` is a term, not a tactic) even before its
    own multi-line indentation broke the enclosing block too. Idempotent on
    text that has no leading `by` at all — the ordinary case for a model's
    reply to `HOLE_PROMPT`, which explicitly asks for "what follows `by`".
    """
    return _LEADING_BY.sub("", text, count=1)


def hole_claims(proof: str) -> list[str]:
    """The claim attached to each `have ... := by sorry`, in order.

    Gives a filler the subgoal in isolation rather than the whole proof.
    Returns "" for a hole that is not part of a `have` — the final tactic,
    for instance — so the list always aligns with `hole_count`.
    """
    claims: list[str] = []
    position = 0
    for hole in _HOLE.finditer(proof or ""):
        segment = proof[position : hole.start()]
        matches = _HAVE.findall(segment)
        claims.append(matches[-1][1].strip() if matches else "")
        position = hole.end()
    return claims


def is_skeleton(proof: str) -> bool:
    """Does this look like a decomposition rather than a finished proof?"""
    return hole_count(proof) > 0


def summarise(proof: str) -> str:
    """One line describing the shape, for a trace."""
    holes = hole_count(proof)
    named = sum(1 for claim in hole_claims(proof) if claim)
    return f"{holes} hole(s), {named} named by a `have`"
