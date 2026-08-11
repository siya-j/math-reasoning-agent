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
        """One line for a prompt, with the first sentence of the docstring.

        The docstring is often the most informative part — Mathlib labels
        `Nat.exists_infinite_primes` as "Euclid's theorem on the infinitude
        of primes", which tells a model far more than the type signature.
        """
        line = f"{self.name}{self.type}".strip()
        summary = (self.doc or "").strip().replace("\n", " ")
        if summary:
            summary = summary.split(". ")[0].strip(" .*")
            if summary:
                line = f"{line}    -- {summary[:110]}"
        return line


def conclusion_of(statement: str) -> str:
    """The part of a Lean theorem after the final top-level `:`.

    This is what the theorem CONCLUDES, and it is the single most informative
    thing about which lemma will close it. Searching identifiers alone loses
    it, with measured consequences:

        goal:  ∃ p, n < p ∧ Nat.Prime p
        |- Nat.Prime _          31 hits, WITHOUT Nat.exists_infinite_primes
        |- ∃ _, _ ∧ Nat.Prime _  1 hit, which IS Nat.exists_infinite_primes

    Binders are skipped by tracking bracket depth, so the `:` inside
    `(n : ℕ)` or `[Group G]` is never mistaken for the conclusion marker.
    """
    depth = 0
    marker = -1
    for index, character in enumerate(statement or ""):
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == ":" and depth == 0:
            # `:=` starts the proof, not the conclusion.
            if index + 1 < len(statement) and statement[index + 1] == "=":
                break
            marker = index
    return statement[marker + 1 :].strip() if marker >= 0 else ""


def conclusion_pattern(statement: str) -> str:
    """A Loogle conclusion query built from the goal's own shape.

    Concrete arguments are replaced by `_` so the pattern matches the general
    lemma rather than one instance of it.
    """
    conclusion = conclusion_of(statement)
    if not conclusion:
        return ""

    def blank(match: re.Match) -> str:
        name = match.group(0)
        # Keep the anchors: dotted names and multi-letter capitalised ones
        # (Nat.Prime, IsCyclic). Blank everything else, including bare
        # capitals — `G` in `IsCyclic G` is a type variable, not a lemma.
        if "." in name or (name[0].isupper() and len(name) > 1):
            return name
        return "_"

    generalised = re.sub(r"[A-Za-z][A-Za-z0-9_'.]*", blank, conclusion)
    generalised = re.sub(r"_\s*:\s*_+", "_", generalised)   # drop `p : ℕ` binders
    generalised = re.sub(r"\s+", " ", generalised).strip()
    return f"|- {generalised}" if generalised.strip("_ ") else ""


_ANCHOR = re.compile(r"[A-Za-z][A-Za-z0-9_']*\.[A-Za-z0-9_']+|[A-Z][A-Za-z0-9_']+")


def conclusion_patterns(statement: str) -> list[str]:
    """Several patterns for one conclusion, loosest last.

    Loogle matches structurally, so a conjunction only matches in the order it
    is written — but a model has no way to know Mathlib's convention. Measured
    on the same goal:

        Gemini wrote   ∃ p, Nat.Prime p ∧ n < p
        Mathlib has    ∃ p, n ≤ p ∧ Nat.Prime p

        |- ∃ _, _ ∧ Nat.Prime _    1 hit  (Nat.exists_infinite_primes)
        |- ∃ _, Nat.Prime _ ∧ _   12 hits (Nat.bertrand, and others that
                                           also close the goal)

    Neither ordering alone is enough, so both are issued. Conjuncts with no
    named anchor are blanked, because `n < p` versus `n ≤ p` should not
    decide whether a lemma is visible.
    """
    exact = conclusion_pattern(statement)
    if not exact:
        return []

    patterns = [exact]
    body = exact[len("|- ") :]

    # Keep any `∃ _,` style prefix intact; permute only the conjunction.
    prefix, _, rest = body.rpartition(",")
    prefix = f"{prefix}," if prefix else ""

    pieces = [piece.strip() for piece in rest.split("∧")]
    if len(pieces) < 2:
        return patterns

    loose = [piece if _ANCHOR.search(piece) else "_" for piece in pieces]
    for ordering in (loose, list(reversed(loose))):
        candidate = f"|- {prefix} {' ∧ '.join(ordering)}".replace("  ", " ").strip()
        if candidate not in patterns:
            patterns.append(candidate)
    return patterns


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
        identifiers = extract_queries(statement, config.MAX_PREMISE_QUERIES)

        # A ladder, most specific first. Every rung is tried and the results
        # are MERGED — earlier first-hit-wins meant one broad query could
        # crowd out the precise one.
        ladder = conclusion_patterns(statement)
        ladder += [f"|- {name} _" for name in identifiers]
        ladder += identifiers

        found: list[Premise] = []
        seen: set[str] = set()
        for query in ladder:
            if not query or len(found) >= config.PREMISE_BUDGET:
                continue
            for premise in self.search(query):
                if premise.name not in seen:
                    seen.add(premise.name)
                    found.append(premise)
        return found[: config.PREMISE_BUDGET]


def render_premises(premises: list[Premise]) -> str:
    """Format premises for a prompt, or say plainly that there are none."""
    if not premises:
        return ""
    lines = "\n".join(f"- {premise.render()}" for premise in premises)
    return (
        "These Mathlib declarations exist and may be useful. Use their exact "
        f"names; do not invent others:\n{lines}\n"
    )
