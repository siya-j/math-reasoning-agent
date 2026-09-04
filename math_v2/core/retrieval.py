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
    # MEASURED on proofnet `exercise_1_18a`. A search for `"inner"` — the
    # inner product, on EuclideanSpace — returned
    #
    #     Array.Matcher.Iterator.inner, Batteries.HashMap.inner,
    #     LieDerivation.inner, MeasureTheory.Measure.InnerRegular
    #
    # Data structures, not mathematics. `Batteries.Tactic` above was too
    # narrow: the whole of `Batteries.` and `Array.` is container plumbing.
    "Batteries.", "Array.",
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


def rank(found):
    """Lemmas before definitions, and no name twice. Fewer, more useful.

    MEASURED on the near-mathlib goal `grp-subgroup-of-cyclic`. Searching
    `"IsCyclic"` returned, in order:

        IsCyclic, IsCyclic.exists_zpow_surjective, IsCyclic.mk,
        not_isCyclic_of_denselyOrdered, ...

    The DEFINITION came first and `IsCyclic.mk` (a constructor) came third, so
    the eight visible slots were spent on things that cannot close a goal. The
    proof, found four attempts later, was `exact inferInstance`.

    Stable within each group: Loogle's own relevance order is better than
    anything invented here, so this only moves definitions down, never
    reshuffles the lemmas.
    """
    from pipeline.tactics import provides_a_proof

    seen = set()
    lemmas, definitions = [], []
    for premise in found:
        if premise.name in seen:
            continue
        seen.add(premise.name)
        (lemmas if provides_a_proof(premise) else definitions).append(premise)
    return lemmas + definitions


def shape_ladder(workdir, search):
    """What the GOAL'S OWN SHAPE returns, for a query that found nothing.

    MEASURED, on eval/results/putnam-run2.json: the agent issued 61 searches
    across five goals. NOT ONE used the `|- <shape>` form, and 22 of them --
    36% -- returned nothing at all. On `putnam_1962_a3` it was 17 empty out of
    26, after which the model gave up and fabricated lemmas. Every one of
    those queries was a bare name: "Routh", "volume", "simplex", "toReal".

    The prompt has always said to search by the conclusion the step produces,
    "because that is what Mathlib is actually indexed by", and
    `loogle.premises_for` has always implemented exactly that ladder --
    hypothesis+conclusion, then conclusion patterns, then names -- with its own
    measured evidence:

        IsCyclic                      2163 hits, wanted lemma not in first 200
        |- IsCyclic _                   54 hits, wanted lemma fourth
        Nat.card _ = _, IsCyclic _      10 hits, wanted lemma FIRST

    `seed_premises` runs that ladder ONCE, when the statement is first checked.
    Nothing ran it again, so a model that searched badly mid-proof simply got
    nothing and moved on. This is the third time in this project that a rule
    stated in the prompt was declined in practice; the answer, as before, is to
    stop asking and start doing.

    Returns [] on any failure. Retrieval is an optimisation and may never take
    a run down.
    """
    statement = log.current_goal(workdir) or log.declared_goal(workdir)
    if not statement or search is None:
        return []
    try:
        found = search.premises_for(statement)
    except Exception:  # noqa: BLE001
        return []
    found, _ = drop_noise([p for p in found if not is_noise(p)])
    return rank(found)


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
    found = rank(found)

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
        # The query found nothing, so spend the turn on the goal's own shape
        # rather than on an apology. See `shape_ladder`.
        rescued = shape_ladder(workdir, search)
        if rescued:
            log.remember_premises(workdir, [
                {"name": p.name, "type": p.type, "module": p.module,
                 "doc": p.doc} for p in rescued
            ])
            log.note(workdir, "search: {!r} -> nothing; goal shape -> {}".format(
                query, ", ".join(p.name for p in rescued[:MAX_SHOWN])))
            return {
                "ok": True,
                "outputs": {
                    "found": [{"name": p.name, "type": p.type}
                              for p in rescued[:MAX_SHOWN]],
                    "suggestions": suggestions,
                    "from_goal_shape": True,
                },
                "message": (
                    "No declarations match {!r}, so this searched the GOAL'S "
                    "OWN SHAPE instead — its conclusion, and its hypotheses "
                    "where they narrow it. That is what Mathlib is indexed "
                    "by, and it is why a bare word usually returns nothing "
                    "useful:\n\n{}{}".format(
                        query, render_premises(rescued[:MAX_SHOWN]), hint)
                ),
            }

        return {
            "ok": True,
            "outputs": {"found": [], "suggestions": suggestions},
            "message": (
                'No declarations match {!r}, and the goal\'s own shape returns '
                'nothing either. Quote a name FRAGMENT ("prime_gt"); leave a '
                "full constant name unquoted.{}".format(query, hint)
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
