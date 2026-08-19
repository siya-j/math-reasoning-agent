"""What a Lean rejection MEANS, and what to do about it. No model, no I/O.

THE GAP THIS CLOSES
-------------------
`interpret` already returns the compiler's error and the goal state, and the
agent already receives them. Measured on the 4-goal ProofNet run, that was not
enough: the errors were read and the response to all of them was the same.

    exercise_1_13a   `by aesop`, then
                     `rcases hc with ⟨c, hc⟩; have h1 : ... := hc; trivial`
    exercise_1_13c   the 30-tactic ladder, then `by rfl`

Every one of those is a generic closer aimed at a goal about holomorphic
functions on a disconnected open set. The information needed to do better was
in the rejection — "Tactic `assumption` failed", "unsolved goals ⊢ f ↑a = f ↑b"
— and nothing turned it into an instruction.

So this module does two deterministic things:

    classify(detail)  -> what KIND of failure this is
    strategy_of(proof) -> what KIND of attempt that was

Both are string inspection. Neither decides whether a proof is correct — Lean
does that, and nothing here can override it. They decide what the agent is
TOLD next, which is the one lever that was not being pulled.
"""

import re

# --------------------------------------------------------------- error kinds
UNKNOWN_IDENTIFIER = "unknown_identifier"
TYPE_MISMATCH = "type_mismatch"
TYPECLASS = "typeclass"
UNSOLVED = "unsolved_goals"
SYNTAX = "syntax"
TACTIC_FAILED = "tactic_failed"
UNCLASSIFIED = "unclassified"

# Ordered: the first match wins, most specific first. Lean often reports
# several, and the FIRST one is the one that has to be fixed before the rest
# are even meaningful.
_PATTERNS = (
    (UNKNOWN_IDENTIFIER, re.compile(
        r"[Uu]nknown (?:identifier|constant)|unknown declaration")),
    (TYPECLASS, re.compile(
        r"failed to synthesize|instance of type class|typeclass instance")),
    (TYPE_MISMATCH, re.compile(
        r"[Aa]pplication type mismatch|type mismatch|argument.*has type")),
    (SYNTAX, re.compile(
        r"unexpected token|expected ['`]|unterminated|unexpected identifier")),
    (TACTIC_FAILED, re.compile(
        r"Tactic `\w+` failed|tactic '\w+' failed|failed to prove the goal")),
    (UNSOLVED, re.compile(r"unsolved goals")),
)

# What each kind should make the agent DO. Written as an instruction rather
# than a description: the agent has already been told what went wrong.
_NEXT_ACTION = {
    UNKNOWN_IDENTIFIER: (
        "The name does not exist in this Mathlib. Do NOT guess a variant — "
        "`search_mathlib` with the name in double quotes as a FRAGMENT, and "
        "use whatever it returns verbatim. Mathlib renames things."
    ),
    TYPECLASS: (
        "Lean could not find an instance, so this is about types and "
        "coercions rather than about your argument. Check what the goal's "
        "objects actually are — a subtype coercion `↑a`, a `Complex` where a "
        "`Real` is wanted — and search for the bridging lemma by shape."
    ),
    TYPE_MISMATCH: (
        "You applied a real lemma with the wrong arguments. Search its exact "
        "name unquoted to see the signature, then supply the arguments in the "
        "order it declares them. Mathlib's order is often not the obvious one."
    ),
    SYNTAX: (
        "This is a parsing failure, not a mathematical one. Repair the syntax "
        "and resubmit — the argument itself has not been judged yet."
    ),
    TACTIC_FAILED: (
        "A named tactic could not close the goal, which tells you the goal is "
        "not of the shape that tactic handles. Do not try a different generic "
        "tactic — read the goal state below and either cite a lemma whose "
        "CONCLUSION matches it, or decompose with `try_skeleton`."
    ),
    UNSOLVED: (
        "The steps you wrote were accepted; what is printed below is what is "
        "still missing. That remaining goal is now your target: search for a "
        "lemma concluding it with `|- `, or make it a `have` and prove it "
        "separately with `try_lemma`."
    ),
    UNCLASSIFIED: "",
}


def classify(detail):
    """Which kind of failure Lean reported. Never raises, never guesses TRUE."""
    text = detail or ""
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return UNCLASSIFIED


def next_action(detail):
    """The instruction that follows from the failure. "" when unclassified."""
    return _NEXT_ACTION.get(classify(detail), "")


# ------------------------------------------------------------ attempt kinds
GENERIC = "generic"          # a bare closer: aesop, simp, trivial, rfl, ...
LADDER = "ladder"            # the `first | ... | ...` tactic ladder
CITATION = "citation"        # exact/apply of a named lemma
STRUCTURED = "structured"    # have/obtain/rcases/induction — an actual argument

# The closers `try_standard_tactics` already runs. Submitting one of these
# alone AFTER the ladder has run buys nothing: the ladder tried it, in the same
# file, and Lean said no.
GENERIC_TACTICS = frozenset({
    "rfl", "trivial", "assumption", "norm_num", "decide", "simp", "simp_all",
    "positivity", "omega", "linarith", "aesop", "tauto", "ring", "field_simp",
    "nlinarith", "constructor", "exact?", "apply?",
})

_STRUCTURE = re.compile(
    r"\b(have|obtain|rcases|rintro|induction|cases|refine|calc|use|intro)\b")
_CITATION = re.compile(r"\b(exact|apply|simpa using|rw)\b")


def strategy_of(proof):
    """Which KIND of attempt this is, for telling near-duplicates apart.

    Deliberately coarse. Two different `have` chains are two attempts and both
    are allowed; two different generic closers are the same idea twice, and the
    second one has already been tried inside the first one's ladder.
    """
    body = (proof or "").strip()
    if not body:
        return GENERIC
    if "first" in body and "|" in body:
        return LADDER

    stripped = re.sub(r"^by\b", "", body).strip()
    # A single bare tactic, possibly with `<;>` chaining, and nothing else.
    words = [w for w in re.split(r"[\s;<>|]+", stripped) if w]
    if words and all(word in GENERIC_TACTICS for word in words):
        return GENERIC

    if _STRUCTURE.search(stripped):
        return STRUCTURED
    if _CITATION.search(stripped):
        return CITATION
    return STRUCTURED


def is_generic(proof):
    """Is this nothing but closers the tactic ladder already ran?"""
    return strategy_of(proof) in (GENERIC, LADDER)
