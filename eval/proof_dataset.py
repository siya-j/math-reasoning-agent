"""Goals for the proving path, tiered by how reachable they should be.

A raw "did it prove it?" number is meaningless without difficulty. A system
that proves `2 + 2 = 4` and nothing else, and one that proves a topology
result, would score identically on a flat list.

    in-mathlib     the theorem exists in the library. Failure means the
                   formalizer or retrieval is at fault, not the mathematics.
    near-mathlib   a few steps from an existing result. Failure means the
                   refinement loop is not doing enough.
    novel          no lemma exists. Failure is expected and uninformative.

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
