"""Get the text out of a model reply, whatever shape it arrived in.

WHY THIS IS NOT ONE LINE. A chat model's reply is not reliably a string.
Across providers and LangChain versions `.content` may be:

    "some text"                                   a plain string
    ["some text"]                                 a list of strings
    [{"type": "text", "text": "some text"}]       a list of content blocks

and `.text` may be a string, a property, or a method depending on the
version. Gemini returns the LIST form, which is how
`AttributeError: 'list' object has no attribute 'strip'` reached a live run
of scripts/check_statements.py after 25 unit tests passed: every fake model
in those tests returned a string, so none of them ever exercised the shape
the real provider actually sends.

The lesson is in the tests next to this file, not only here: a fake that is
tidier than the real thing tests the fake.

ONE IMPLEMENTATION, several callers. scripts/contamination.py had its own
version of this, which worked by trying `.text` first and would have
returned a list had that attribute been absent. The same reasoning that
keeps `unsupported_in` in a single place in pipeline/faithfulness.py applies
here: a second copy drifts, and this one had already started to.
"""

from __future__ import annotations


def text_of(reply) -> str:
    """The assistant's text, or "" if there is none.

    Never raises. A reply that cannot be read is an absence of text, and
    callers decide what that means; turning it into an exception would make
    every one of them handle a provider detail.
    """
    if reply is None:
        return ""
    if isinstance(reply, str):
        return reply

    # `.text` is the flattened form where the version provides it. It is a
    # property on modern LangChain and a method on older ones.
    text = getattr(reply, "text", None)
    if callable(text):
        try:
            text = text()
        except Exception:
            text = None
    if isinstance(text, str) and text:
        return text

    return _flatten(getattr(reply, "content", reply))


def _flatten(content) -> str:
    """Join content blocks into one string."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _from_block(content)
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else _from_block(part)
            for part in content
        )
    return ""


def _from_block(block) -> str:
    """The text of a single content block, ignoring images and tool calls."""
    if not isinstance(block, dict):
        return ""
    for key in ("text", "content"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    return ""
