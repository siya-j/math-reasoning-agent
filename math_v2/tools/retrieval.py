"""Mathlib search. In-process — no container, no dispatch.

NO `from __future__ import annotations` (§5.1, gotcha 1).

There is no network inside a SIF (§7.3) and Loogle is an HTTP service, so this
is the in-process shape from §5.3 — the `hermes_v2` pattern. The underlying
client is synchronous urllib, so it is run in a thread rather than blocking the
event loop for up to the Loogle timeout.
"""

import asyncio

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from math_v2.context import MathContext
from math_v2.core import budget, retrieval

_SEARCH = None


def get_search():
    """One client for the process. Built lazily; failure is not fatal.

    Retrieval is an optimisation. Without it the model guesses lemma names,
    which is worse but not broken, so nothing here may take a run down.
    """
    global _SEARCH
    if _SEARCH is None:
        try:
            from retrieval.loogle import LoogleSearch

            _SEARCH = LoogleSearch()
        except Exception:  # noqa: BLE001
            _SEARCH = False
    return _SEARCH or None


@tool
async def search_mathlib(query: str, runtime: ToolRuntime[MathContext]) -> dict:
    """Search Mathlib for declarations, by name or by shape.

    Use this whenever you need a lemma and are not certain of its exact name or
    signature. Search as often as you like — it costs milliseconds, where
    guessing a name that does not exist costs a whole compilation. If a name
    you expected is not found, the results include Mathlib names close to it;
    read them, because the library renames things.

    Args:
        query: one of three forms, and the difference matters.
            "fragment" — IN DOUBLE QUOTES, any declaration whose NAME contains
                that text. Use this when unsure of a name. An unquoted fragment
                is read as a full constant name and will simply not be found.
            Full.Name — an exact constant, unquoted.
            |- pattern — declarations whose CONCLUSION matches, which is
                usually what closes a goal. A hypothesis shape and a conclusion
                may be separated by a comma.
    """
    workdir = runtime.context.workdir
    stop = budget.spend(workdir, search=True)
    if stop:
        return stop
    return await asyncio.to_thread(
        retrieval.search_mathlib, workdir, query, get_search()
    )
