"""Turning real source material into candidate benchmark cases.

WHY. Everything in eval/golden-science.json was invented by this project.
Probing the machinery against paper-grade checks failed six times out of
eight, because a benchmark written by the same people who wrote the
verifiers tests what they already thought of. The fix is material nobody
here chose: corrigenda, arXiv v1->v2 change notes, supplementary
calculations, questions researchers actually asked.

WHAT THIS ANSWERS FIRST, and it is not "how many cases can we add".

    Of the numerical work a scientist actually does, what fraction is this
    architecture even SHAPED for?

The system decides stated claims. A researcher usually has no claim — they
have a quantity to compute. If most real material turns out to be
computations rather than claims, that is a finding about the ARCHITECTURE,
and it is worth more than another fifty cases. So the triage separates:

    CLAIM          a stated assertion a verifier could settle as-is
    COMPUTATION    a quantity to work out; there is no claim to check, and
                   the system as built would have to be given a guess first
    OUT OF REACH    quantitative, but no verifier here could decide it
    NOT QUANTITATIVE  prose, method, interpretation

NOTHING HERE BECOMES A BENCHMARK CASE ON ITS OWN. The output is a draft for
a human to accept or throw away. A model deciding what our benchmark should
contain would be the same mistake as a model deciding what is true, one
level removed — and the last time this project let an invented expectation
into a measurement it took a live run and a Lean session to find.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from llm.reply import text_of

CLAIM = "claim"
COMPUTATION = "computation"
OUT_OF_REACH = "out_of_reach"
NOT_QUANTITATIVE = "not_quantitative"

KINDS = (CLAIM, COMPUTATION, OUT_OF_REACH, NOT_QUANTITATIVE)


def available_tools() -> list[str]:
    """The checks that actually exist, read from the tool list itself.

    Derived rather than written down. A hardcoded list would drift from the
    tools, and the model would be told a capability exists that it cannot
    reach — which is exactly the bug that made two correct claims score as
    soundness failures on the first science run.
    """
    from pipeline.tools import VerificationLog, make_tools

    return sorted(tool.__name__ for tool in make_tools(VerificationLog()))


PROMPT = """You are sorting real scientific source material to find out what
kind of numerical work it contains.

Here is the material:

---
{text}
---

The system doing the sorting can run these checks, and nothing else:

{tools}

Answer in exactly this format, four lines, nothing else:

KIND: claim
TOOL: check_quantity
WHAT: one sentence saying what is being asserted or asked
WHY: one sentence on why you chose that kind

KIND must be exactly one of:

  claim            a STATED assertion, with a specific value or relation
                   already given, that one of the checks above could settle
                   as it stands.
  computation      a quantity to WORK OUT. No value is asserted, so there is
                   nothing to check — a checker would first have to be handed
                   a guess. Choose this whenever the material asks "what is"
                   rather than "is it true that".
  out_of_reach     quantitative, but none of the checks above could decide
                   it — it needs a method, a simulation, a measurement, or
                   mathematics beyond them.
  not_quantitative prose, methodology, interpretation, or opinion.

TOOL must be one of the names listed above, or `none`. Give a tool only when
you are confident that check could settle this exact thing. `none` is a
perfectly good answer and is much better than a wrong one.

Do not be generous. Marking something a claim when it is really a request to
compute is the error that matters here, because it would hide the gap this
sorting exists to measure."""


@dataclass(frozen=True)
class Item:
    id: str
    text: str
    source: str = ""


@dataclass(frozen=True)
class Triage:
    id: str
    source: str
    kind: str
    tool: str
    what: str
    why: str
    text: str = ""

    @property
    def usable_today(self) -> bool:
        return self.kind == CLAIM and self.tool not in ("", "none")


def _field(text: str, name: str) -> str:
    found = re.search(rf"^\s*{name}\s*:\s*(.+)$", text or "",
                      re.MULTILINE | re.IGNORECASE)
    return found.group(1).strip() if found else ""


def parse_triage(reply: str, tools: list[str]) -> tuple[str, str, str, str]:
    """Read the four lines. Anything unreadable is NOT a classification.

    An unparseable answer becomes OUT_OF_REACH with the reason recorded,
    never CLAIM. Defaulting the other way would quietly inflate the one
    number this whole exercise exists to measure.
    """
    kind = _field(reply, "KIND").lower().replace(" ", "_")
    tool = _field(reply, "TOOL").strip()
    what = _field(reply, "WHAT")
    why = _field(reply, "WHY")

    if kind not in KINDS:
        return OUT_OF_REACH, "", what, (
            why or "the classification could not be read"
        )
    # A tool the model invented is worse than no tool: it would send a
    # reviewer looking for a capability that does not exist.
    if tool not in tools:
        tool = ""
    return kind, tool, what, why


def triage(item: Item, model, tools: list[str] | None = None) -> Triage:
    """Sort one piece of source material. Never raises."""
    tools = tools if tools is not None else available_tools()
    listing = "\n".join(f"  {name}" for name in tools)

    if not (item.text or "").strip():
        return Triage(item.id, item.source, NOT_QUANTITATIVE, "", "",
                      "the item is empty")

    try:
        reply = text_of(model.invoke(PROMPT.format(text=item.text.strip(),
                                                   tools=listing)))
    except Exception as exc:
        return Triage(item.id, item.source, OUT_OF_REACH, "", "",
                      f"could not be sorted: {exc}", item.text)

    kind, tool, what, why = parse_triage(reply, tools)
    return Triage(item.id, item.source, kind, tool, what, why, item.text)


def load_items(path) -> list[Item]:
    """Read source material.

    Accepts a JSON list of {id, text, source}, or a plain text file with
    items separated by a line of three or more dashes -- because the point
    is to make pasting real material easy, and a format that demands JSON
    from a human with a PDF open will not get used.
    """
    from pathlib import Path

    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        chunks = [c.strip() for c in re.split(r"^-{3,}\s*$", raw, flags=re.M)]
        return [Item(id=f"item-{i}", text=chunk)
                for i, chunk in enumerate(chunks, 1) if chunk]

    return [
        Item(id=str(row.get("id", f"item-{i}")),
             text=str(row.get("text", "")),
             source=str(row.get("source", "")))
        for i, row in enumerate(data, 1)
    ]


@dataclass
class Summary:
    counts: dict = field(default_factory=dict)
    by_tool: dict = field(default_factory=dict)
    total: int = 0

    @property
    def shaped_for(self) -> float:
        """The fraction this architecture could settle as it stands.

        THE number this exercise produces. Everything else is detail.
        """
        if not self.total:
            return 0.0
        return self.counts.get(CLAIM, 0) / self.total


def summarize(triaged: list[Triage]) -> Summary:
    summary = Summary(total=len(triaged))
    for item in triaged:
        summary.counts[item.kind] = summary.counts.get(item.kind, 0) + 1
        if item.usable_today:
            summary.by_tool[item.tool] = summary.by_tool.get(item.tool, 0) + 1
    return summary
