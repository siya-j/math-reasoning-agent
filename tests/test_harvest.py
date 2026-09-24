"""Sorting real source material.

The question this tooling answers is not "how many cases can we harvest".
It is what fraction of real scientific numerical work this architecture is
SHAPED for -- stated claims it can check, against quantities a researcher
wants computed, for which it would first have to be handed a guess.

So the tests care most about the classifier being CONSERVATIVE. Marking a
computation as a claim would inflate the one number the exercise exists to
measure, which is the failure that matters here.
"""

import json

import pytest

from eval import harvest
from eval.harvest import CLAIM, COMPUTATION, NOT_QUANTITATIVE, OUT_OF_REACH, Item

TOOLS = ["check_numeric", "check_quantity", "check_uncertainty"]


class Model:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else ""


def sorted_as(reply: str, text: str = "some material"):
    return harvest.triage(Item("i", text), Model(reply), TOOLS)


# ------------------------------------------------------- the tools are real
def test_the_available_tools_are_read_from_the_tool_list():
    """Derived, not written down. A hardcoded list drifts from the tools and
    tells the model a capability exists that it cannot reach -- the bug that
    made two correct claims score as soundness failures on the first run."""
    from pipeline.tools import VerificationLog, make_tools

    assert harvest.available_tools() == sorted(
        tool.__name__ for tool in make_tools(VerificationLog())
    )


def test_the_prompt_lists_only_checks_that_exist():
    model = Model("KIND: claim\nTOOL: check_numeric\nWHAT: x\nWHY: y")
    harvest.triage(Item("i", "text"), model, TOOLS)
    for tool in TOOLS:
        assert tool in model.prompts[0]
    assert "check_invented" not in model.prompts[0]


# ------------------------------------------------------------- the sorting
def test_a_stated_claim_is_usable_today():
    result = sorted_as(
        "KIND: claim\nTOOL: check_numeric\nWHAT: asserts 2+2=4\nWHY: stated"
    )
    assert result.kind == CLAIM
    assert result.usable_today


def test_a_computation_is_not_usable_even_with_a_tool():
    """THE distinction. There is no claim to check, so a checker would have
    to be handed a guess first. Counting it as usable would hide the gap."""
    result = sorted_as(
        "KIND: computation\nTOOL: check_numeric\nWHAT: asks for g\nWHY: no value"
    )
    assert result.kind == COMPUTATION
    assert not result.usable_today


def test_a_claim_with_no_tool_is_not_usable():
    result = sorted_as("KIND: claim\nTOOL: none\nWHAT: x\nWHY: y")
    assert result.kind == CLAIM
    assert not result.usable_today


def test_an_invented_tool_is_discarded():
    """Worse than no tool: it sends a reviewer looking for a capability that
    does not exist."""
    result = sorted_as("KIND: claim\nTOOL: check_telepathy\nWHAT: x\nWHY: y")
    assert result.tool == ""
    assert not result.usable_today


@pytest.mark.parametrize(
    "reply",
    ["", "I think it's a claim!", "KIND: maybe\nTOOL: none",
     "KIND: \nTOOL: \nWHAT: \nWHY: "],
)
def test_an_unreadable_reply_never_becomes_a_claim(reply):
    """Defaulting the other way would quietly inflate the one number this
    exercise exists to measure."""
    assert sorted_as(reply).kind != CLAIM


def test_a_failure_to_sort_is_recorded_not_guessed():
    class Broken:
        def invoke(self, prompt):
            raise RuntimeError("the provider said no")

    result = harvest.triage(Item("i", "text"), Broken(), TOOLS)
    assert result.kind == OUT_OF_REACH
    assert "could not be sorted" in result.why


def test_an_empty_item_is_not_quantitative():
    result = harvest.triage(Item("i", "   "), Model(), TOOLS)
    assert result.kind == NOT_QUANTITATIVE


# -------------------------------------------------------------- the loading
def test_json_source_material_loads(tmp_path):
    path = tmp_path / "items.json"
    path.write_text(json.dumps([
        {"id": "corr-1", "source": "J. Chem. Phys.", "text": "Eq. 12 should read"},
    ]))
    items = harvest.load_items(path)
    assert items[0].id == "corr-1"
    assert items[0].source == "J. Chem. Phys."


def test_plain_text_split_on_dashes_loads(tmp_path):
    """A format that demands JSON from someone with a PDF open will not get
    used."""
    path = tmp_path / "items.txt"
    path.write_text("first item\n---\nsecond item\n-----\nthird item\n")
    items = harvest.load_items(path)
    assert [i.text for i in items] == ["first item", "second item", "third item"]


def test_empty_chunks_are_dropped(tmp_path):
    path = tmp_path / "items.txt"
    path.write_text("one\n---\n\n---\n   \n---\ntwo\n")
    assert len(harvest.load_items(path)) == 2


# ------------------------------------------------------------- the headline
def test_shaped_for_counts_only_stated_claims():
    triaged = [
        sorted_as("KIND: claim\nTOOL: check_numeric\nWHAT: a\nWHY: b"),
        sorted_as("KIND: computation\nTOOL: check_numeric\nWHAT: a\nWHY: b"),
        sorted_as("KIND: computation\nTOOL: none\nWHAT: a\nWHY: b"),
        sorted_as("KIND: out_of_reach\nTOOL: none\nWHAT: a\nWHY: b"),
    ]
    summary = harvest.summarize(triaged)
    assert summary.total == 4
    assert summary.shaped_for == 0.25
    assert summary.counts[COMPUTATION] == 2


def test_the_tool_breakdown_counts_only_usable_items():
    triaged = [
        sorted_as("KIND: claim\nTOOL: check_quantity\nWHAT: a\nWHY: b"),
        sorted_as("KIND: claim\nTOOL: check_quantity\nWHAT: a\nWHY: b"),
        sorted_as("KIND: computation\nTOOL: check_quantity\nWHAT: a\nWHY: b"),
    ]
    assert harvest.summarize(triaged).by_tool == {"check_quantity": 2}


def test_nothing_sorted_is_an_empty_summary_not_a_crash():
    summary = harvest.summarize([])
    assert summary.total == 0 and summary.shaped_for == 0.0
