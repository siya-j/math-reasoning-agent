"""Mathlib premise search via Loogle's JSON API.

Loogle indexes Mathlib and answers over HTTP, which makes this the one piece
of Phase 6 that needs neither a Lean installation nor an API key.

    https://loogle.lean-lang.org/json?q=Nat.Prime

Two response shapes, both observed against the live service:

    {"count": 2163, "header": "...", "hits": [
        {"name": "Nat.Prime", "type": " (p : ℕ) : Prop",
         "module": "Mathlib.Data.Nat.Prime.Defs", "doc": "..."}]}

    {"error": "<input>:1:3: unexpected end of input; ...", "heartbeats": 0}

NOTHING HERE RAISES
-------------------
A failed search is not an error, it is an empty result. Retrieval is an
optimisation: without it the model guesses lemma names, which is exactly what
it did before this module existed. A network outage must never take down a
proof attempt, and must certainly never affect a verdict.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import config

# Mathlib identifiers: capitalised, optionally dotted — Nat.Prime, IsCyclic,
# Group. Lowercase lemma names are deliberately excluded; they are usually
# what we are trying to FIND, not useful as a query.
_IDENTIFIER = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_']+)*")

# Names that appear in almost every statement and retrieve nothing useful.
_TOO_COMMON = frozenset({"Type", "Prop", "Sort", "Set", "Nat", "Int", "Real"})


@dataclass(frozen=True)
class Premise:
    name: str
    type: str = ""
    module: str = ""
    doc: str = ""

    def render(self) -> str:
        """One compact line, for pasting into a prompt."""
        return f"{self.name}{self.type}".strip()


def extract_queries(statement: str, limit: int) -> list[str]:
    """Which Mathlib names to look up, given a formal statement.

    Deterministic on purpose. Asking the model what to search for would put
    a guess in front of the lookup that exists to replace guessing.
    """
    seen: list[str] = []
    for match in _IDENTIFIER.findall(statement or ""):
        # A bare capital is a type variable — G, R, M in `(G : Type*)`. Those
        # name nothing in Mathlib, and searching them wastes a lookup.
        if len(match) < 2:
            continue
        if match in _TOO_COMMON or match in seen:
            continue
        seen.append(match)
    return seen[:limit]


class LoogleSearch:
    """A thin client. `fetch` is injected so tests never touch the network."""

    def __init__(self, url: str | None = None, timeout: int | None = None, fetch=None):
        self._url = url or config.LOOGLE_URL
        self._timeout = timeout or config.LOOGLE_TIMEOUT
        self._fetch = fetch or self._http_get

    def _http_get(self, url: str) -> str:
        request = urllib.request.Request(
            url, headers={"User-Agent": "math-reasoning-agent"}
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return response.read().decode("utf-8")

    def search(self, query: str, limit: int | None = None) -> list[Premise]:
        """Declarations matching `query`, or [] if anything at all goes wrong."""
        limit = limit or config.PREMISES_PER_QUERY
        if not query.strip():
            return []

        url = f"{self._url}?q={urllib.parse.quote(query)}"
        try:
            payload = json.loads(self._fetch(url))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return []

        if not isinstance(payload, dict) or "error" in payload:
            return []

        return [
            Premise(
                name=hit.get("name", ""),
                type=hit.get("type", ""),
                module=hit.get("module", ""),
                doc=(hit.get("doc") or ""),
            )
            for hit in payload.get("hits", [])[:limit]
            if hit.get("name")
        ]

    def premises_for(self, statement: str) -> list[Premise]:
        """Look up every promising identifier in a formal statement.

        Conclusion patterns first. Measured against the live service for
        `IsCyclic`:

            IsCyclic        2163 hits, mostly definitions and instances;
                            the theorem we wanted was not in the first 200
            |- IsCyclic _     54 hits, and `isCyclic_of_prime_card`
                            ("A finite group of prime order is cyclic")
                            came fourth

        `|- X _` asks for declarations that CONCLUDE X — which is what
        proving needs. A plain name search asks for anything mentioning X,
        which is dominated by the definition and its instances.
        """
        found: list[Premise] = []
        seen: set[str] = set()
        for identifier in extract_queries(statement, config.MAX_PREMISE_QUERIES):
            for query in (f"|- {identifier} _", identifier):
                hits = self.search(query)
                if not hits:
                    continue  # fall back to the plain name search
                for premise in hits:
                    if premise.name not in seen:
                        seen.add(premise.name)
                        found.append(premise)
                break
        return found


def render_premises(premises: list[Premise]) -> str:
    """Format premises for a prompt, or say plainly that there are none."""
    if not premises:
        return ""
    lines = "\n".join(f"- {premise.render()}" for premise in premises)
    return (
        "These Mathlib declarations exist and may be useful. Use their exact "
        f"names; do not invent others:\n{lines}\n"
    )
