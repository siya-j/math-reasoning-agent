"""Mathlib premise search. In-process, no container.

NO `from __future__ import annotations` (blueprint §5.1).

WHY THIS ONE IS NOT DISPATCHED
------------------------------
There is no network inside a SIF (blueprint §7.3), and Loogle is an HTTP call
to loogle.lean-lang.org. So this is the in-process shape from §5.3 — the
`hermes_v2` pattern — and it has no business on the compute queue anyway: a
200 ms lookup does not need a container.

Synchronous on purpose. `retrieval/loogle.py` is a dependency-free urllib
client that never raises, so the `@tool` wrapper should call this inside
`asyncio.to_thread` rather than block the event loop for up to the Loogle
timeout.
"""

from retrieval.loogle import render_premises

from math_v2.core import log

MAX_SHOWN = 8

# Modules that are Lean/Mathlib MACHINERY, never proof content. Measured: a
# search for "constant" returned Std.Sat.AIG.getConstant, Lean.ConstantInfo and
# Lean.instBEqConstantVal; "re" returned Lean.defaultMaxRecDepth and
# Lean.Macro.MethodsRef. Eight slots of a result list, none of them usable in a
# proof, and each wasted list cost the agent a ~30s model turn to read and
# another to search again.
NOISE = (
    "Lean.", "Std.", "Init.", "Qq.", "Aesop.", "Plausible.",
    "Mathlib.Tactic", "Batteries.Tactic",
)


def is_noise(premise) -> bool:
    """Metaprogramming rather than mathematics."""
    where = f"{getattr(premise, 'module', '')}.{getattr(premise, 'name', '')}"
    return any(part in where for part in NOISE)


def drop_noise(found):
    """Filter machinery out of a result list — but NEVER down to nothing.

    A filter that can empty the list would turn a poor search into a silent
    one, and the agent cannot tell those apart. If everything matched the
    noise patterns the query itself was wrong, and seeing that is the useful
    signal.
    """
    kept = [p for p in found if not is_noise(p)]
    return (kept, len(found) - len(kept)) if kept else (found, 0)


def search_mathlib(workdir, query, search, limit=None):
    """Search Mathlib, remember what came back, and pass on Loogle's own hints.

    A bare identifier is a CONSTANT lookup; a name FRAGMENT must be quoted.
    Loogle answers an unparseable query with an error AND a suggestion list,
    and the client retries with the first suggestion, returning the rest.
    Measured before that was fixed: 19 of 20 searches returned nothing while
    Loogle had named the right lemma every time.
    """
    if search is None:
        return {
            "ok": True,
            "outputs": {"found": []},
            "message": "Search is unavailable. Rely on names you are certain of.",
        }

    found, suggestions = search.search_with_suggestions(query, limit=limit)
    found, dropped = drop_noise(found)

    log.remember_premises(workdir, [
        {"name": p.name, "type": p.type, "module": p.module, "doc": p.doc}
        for p in found
    ])
    # Names, not just a count: a count cannot distinguish "retrieval never
    # surfaced the lemma" from "retrieval surfaced it and the agent ignored
    # it", and those call for opposite fixes.
    log.note(workdir, "search: {!r} -> {}".format(
        query, ", ".join(p.name for p in found[:MAX_SHOWN]) or "nothing"
    ))

    hint = (
        "\n\nLoogle also knows these similar names: " + ", ".join(suggestions)
        if suggestions else ""
    )
    if dropped:
        hint += (
            f"\n({dropped} compiler-internal result(s) hidden. If a query keeps "
            "returning machinery, it is too generic — search for the SHAPE of "
            "the goal with `|- ` instead of a bare word.)"
        )

    if not found:
        return {
            "ok": True,
            "outputs": {"found": [], "suggestions": suggestions},
            "message": (
                'No declarations match {!r}. Quote a name FRAGMENT ("prime_gt"); '
                "leave a full constant name unquoted.{}".format(query, hint)
            ),
        }

    return {
        "ok": True,
        "outputs": {
            "found": [{"name": p.name, "type": p.type} for p in found],
            "suggestions": suggestions,
        },
        "message": render_premises(found) + hint,
    }
