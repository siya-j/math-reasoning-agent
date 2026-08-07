"""The golden dataset (Design Doc section 9 — Evaluation).

Cases are stored as JSON so the dataset can grow without touching code.
`expected` is what a CORRECT system should conclude:

    "true" / "false"  a deterministic verifier should decide it
    "n/a"             nothing available can decide it, and the agent must
                      NOT claim otherwise
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from domain.verdict import VerificationStatus

GOLDEN_PATH = Path(__file__).with_name("golden.json")


@dataclass(frozen=True)
class GoldenCase:
    id: str
    area: str
    question: str
    expected: VerificationStatus
    note: str = ""


def load_cases(path: Path | None = None) -> list[GoldenCase]:
    """Read the golden dataset from disk."""
    raw = json.loads((path or GOLDEN_PATH).read_text())
    return [
        GoldenCase(
            id=item["id"],
            area=item["area"],
            question=item["question"],
            expected=VerificationStatus(item["expected"]),
            note=item.get("note", ""),
        )
        for item in raw
    ]
