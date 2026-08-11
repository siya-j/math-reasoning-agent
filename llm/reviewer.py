"""Statement-preservation review — the last defence against answering the
wrong question.

Lean guarantees a proof is correct. Nothing guarantees the STATEMENT says what
the user asked. That gap produced failures 3 and 8 in this project, and it is
the gap the faithfulness lint only partially closes (it compares numbers, so
it cannot see `sin` swapped for `cos`).

THE CONSTRAINT: THIS CAN ONLY REFUSE
------------------------------------
The reviewer may downgrade a verdict to UNKNOWN. It may NEVER produce a TRUE.

Two independent findings force this:

  AI Co-Mathematician (2605.06651) — optimising against a reviewer can
    "converge to an argument that remains flawed, but where the errors can no
    longer be detected by the reviewer agent". A gate teaches the system to
    produce arguments that gate cannot catch.

  miniF2F Revisited (2511.03108) — an LLM judge rated formalisations 97.5%
    correct where human experts found 62.7%.

A reviewer that can only refuse is safe under both. A reviewer that can approve
is a new way to be confidently wrong.

FAILING OPEN, BUT DISCLOSED
---------------------------
If the model is unreachable or its answer is unparseable, review does not
happen and raises no concern — it is an ADDITIONAL layer on top of the
deterministic checks, and a missing extra layer must not invalidate the ones
that did run. But `Review.performed` records that it did not happen, so a
report never implies a scrutiny it did not receive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from llm.client import get_model
from llm.retry import call_with_backoff

REVIEW_PROMPT = """Compare a mathematical question with its formal translation.

The question, in English:
{question}

The formal statement produced from it:
{statement}

Decide ONLY whether the formal statement asserts the same thing as the
question. Do not judge whether either is true. Do not judge the proof.

Look specifically for:
- a claim that was strengthened, weakened, or narrowed
- assumptions added that the question did not state
- a different quantifier (some vs every, exists vs all)
- a different function, operation or constant substituted
- a question that asked whether something is the ONLY case, translated as
  whether it is A case

Answer in exactly this format, two lines, nothing else:

VERDICT: matches
CONCERN: none

or

VERDICT: differs
CONCERN: one sentence naming exactly what differs"""

_FIELD = r"^\s*{}\s*:\s*(.+)$"


@dataclass(frozen=True)
class Review:
    """What review found. There is deliberately no `approved` field."""

    performed: bool = False
    concerns: list[str] = field(default_factory=list)

    @property
    def objected(self) -> bool:
        return bool(self.concerns)

    def note(self) -> str:
        if not self.performed:
            return "statement not reviewed"
        if self.concerns:
            return f"review objected: {self.concerns[0]}"
        return "reviewed, no objection raised"


def _field_value(text: str, name: str) -> str:
    match = re.search(_FIELD.format(name), text or "", re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def parse_review(text: str) -> Review:
    """Read the model's two lines.

    Anything other than an explicit `differs` raises no concern. That is the
    fail-open rule: an unparseable reviewer must not block a proof the
    deterministic checks already accepted.
    """
    verdict = _field_value(text, "VERDICT").lower()
    concern = _field_value(text, "CONCERN")

    if "differs" not in verdict:
        return Review(performed=True)
    if not concern or concern.lower() in {"none", "n/a"}:
        concern = "the reviewer reported a mismatch without naming it"
    return Review(performed=True, concerns=[concern])


class Reviewer:
    """One model call: question and statement in, concerns out."""

    def __init__(self, model=None):
        self._model = model or get_model()

    def review(self, question: str, statement: str) -> Review:
        if not question.strip() or not statement.strip():
            return Review(performed=False)
        try:
            reply = call_with_backoff(
                lambda: self._model.invoke(
                    REVIEW_PROMPT.format(question=question, statement=statement)
                )
            )
            text = getattr(reply, "text", None) or getattr(reply, "content", "") or ""
        except Exception:
            # Unreachable reviewer: no review, no concern, and it is recorded.
            return Review(performed=False)
        return parse_review(text)
