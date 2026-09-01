"""Goals for the proving path, tiered by how reachable they should be.

A raw "did it prove it?" number is meaningless without difficulty. A system
that proves `2 + 2 = 4` and nothing else, and one that proves a topology
result, would score identically on a flat list.

    in-mathlib     the theorem exists in the library. Failure means the
                   formalizer or retrieval is at fault, not the mathematics.
    near-mathlib   a few steps from an existing result. Failure means the
                   refinement loop is not doing enough.
    novel          no lemma exists. Failure is expected and uninformative.
    hard           no one-shot lemma exists (checked against live Loogle,
                   per docs/hard-tier-candidates.md), and unlike novel each
                   goal names what a specific outcome would mean — e.g. a
                   failure with no `computation` record before the first
                   compile says the SymPy bridge is unused, not that the
                   goal is out of reach. Added because near-mathlib stopped
                   discriminating: it scored 7/7 while only one of its seven
                   goals needed an actual mathematical idea.
    deep           a SIMPLE statement whose proof necessarily needs advanced
                   machinery — not a tedious elementary proof that advanced
                   methods merely shorten (per docs/deep-tier-candidates.md,
                   every claim checked against the local Mathlib source, not
                   assumed). Different question from `hard`: that tier asks
                   whether the agent can find an idea Mathlib doesn't state
                   directly; this one asks whether it recognises that ONLY a
                   specific piece of deep machinery (a named theorem, not a
                   tactic) can possibly close the goal, and finds it.

The `mathlib` field records the theorem's name where it is known, so a
failure can be attributed: if the lemma is there and the model missed it,
that is a retrieval or prompting problem, not a mathematical one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

GOALS_PATH = Path(__file__).parent / "proofs.json"


class Tier(str, Enum):
    IN_MATHLIB = "in-mathlib"
    NEAR_MATHLIB = "near-mathlib"
    NOVEL = "novel"
    HARD = "hard"
    DEEP = "deep"
    # External benchmark, loaded from its own file via --goals. Our curated
    # tiers are goals we chose; this one is not, which is the point.
    PROOFNET = "proofnet"
    # PutnamBench (github.com/trishullab/PutnamBench): 672 competition
    # problems, the Lean statement always given. Unlike ProofNet, there is no
    # informal-only mode -- the formal statement is the whole point of this
    # benchmark, so `--mode` does not exist on eval/putnam.py.
    PUTNAM = "putnam"


@dataclass(frozen=True)
class Goal:
    id: str
    area: str
    goal: str
    tier: Tier
    mathlib: str | None = None
    note: str = ""


def load_goals(path: Path | None = None) -> list[Goal]:
    raw = json.loads((path or GOALS_PATH).read_text(encoding="utf-8"))
    return [
        Goal(
            id=entry["id"],
            area=entry["area"],
            goal=entry["goal"],
            tier=Tier(entry["tier"]),
            mathlib=entry.get("mathlib"),
            note=entry.get("note", ""),
        )
        for entry in raw
    ]
