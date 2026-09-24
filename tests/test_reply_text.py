"""Getting text out of a model reply, whatever shape it arrived in.

WRITTEN AFTER A LIVE FAILURE. `scripts/check_statements.py` died on its
first real call with

    AttributeError: 'list' object has no attribute 'strip'

after 25 unit tests had passed. Every fake model in those tests returned a
string; Gemini returns a LIST of content blocks. The tests were exercising
the fake, not the provider.

So these cases are the shapes providers actually send, and the point of the
file is that a fake tidier than the real thing proves nothing.
"""

import pytest

from llm.reply import text_of


class Reply:
    """A message object with only the attributes named."""

    def __init__(self, **attributes):
        for name, value in attributes.items():
            setattr(self, name, value)


# ------------------------------------------------------- the shapes that ship
def test_a_plain_string_content():
    assert text_of(Reply(content="hello")) == "hello"


def test_a_list_of_content_blocks():
    """Gemini. The shape that broke the live run."""
    assert text_of(Reply(content=[{"type": "text", "text": "hello"}])) == "hello"


def test_several_blocks_are_joined():
    assert text_of(Reply(content=[
        {"type": "text", "text": "hello "},
        {"type": "text", "text": "world"},
    ])) == "hello world"


def test_a_list_of_bare_strings():
    assert text_of(Reply(content=["hello ", "world"])) == "hello world"


def test_the_text_attribute_wins_when_present():
    """Modern LangChain exposes `.text` already flattened."""
    assert text_of(Reply(text="flat", content=[{"text": "blocks"}])) == "flat"


def test_text_as_a_method_on_older_versions():
    assert text_of(Reply(text=lambda: "called", content="ignored")) == "called"


def test_a_broken_text_method_falls_back_to_content():
    def explode():
        raise RuntimeError("no")

    assert text_of(Reply(text=explode, content="fallback")) == "fallback"


def test_a_bare_string_is_itself():
    assert text_of("hello") == "hello"


# ---------------------------------------------- non-text parts are not text
def test_image_and_tool_blocks_are_skipped_not_stringified():
    """A tool-call block has no text. Rendering its dict would put JSON into
    a sentence a judge is about to read."""
    assert text_of(Reply(content=[
        {"type": "text", "text": "look: "},
        {"type": "image_url", "image_url": {"url": "http://x"}},
        {"type": "tool_use", "id": "1", "name": "f", "input": {}},
    ])) == "look: "


# ------------------------------------------------ absence, never an exception
@pytest.mark.parametrize(
    "reply",
    [None, Reply(content=None), Reply(content=[]), Reply(content=""),
     Reply(), Reply(content=[{"type": "image_url"}]), 42],
)
def test_nothing_readable_is_an_empty_string_not_a_crash(reply):
    """Callers decide what an empty reply means. Raising would make every
    one of them handle a provider detail."""
    assert text_of(reply) == ""


# ------------------------------------------------- the callers that need it
def test_the_round_trip_survives_a_list_returning_model():
    """The exact live failure, as a test."""
    from eval import roundtrip

    class Gemini:
        """Returns content the way the real provider does."""

        def __init__(self, *texts):
            self.texts = list(texts)

        def invoke(self, prompt):
            return Reply(content=[{"type": "text", "text": self.texts.pop(0)}])

    result = roundtrip.assess(
        "g", "suspect_statement",
        "theorem t (n : Nat) : n + 0 = n",
        "Adding zero changes nothing.",
        Gemini("n plus zero is n.", "VERDICT: match\nWHY: same claim."),
    )
    assert result.verdict == roundtrip.MATCH
    assert result.back_translation == "n plus zero is n."
