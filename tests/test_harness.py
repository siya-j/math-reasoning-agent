"""Offline tests for harness selection.

No model, no LangChain agent, no deepagents. What matters here is that
swapping the harness cannot reach the verdict — and that the switch fails
loudly rather than silently falling back.
"""

import pytest

import config
from domain.verdict import VerificationStatus as S
from pipeline import guard, harness
from pipeline.harness import DEEPAGENTS, LANGCHAIN, build_agent, final_text
from pipeline.tools import VerificationLog, make_tools


class Message:
    def __init__(self, text="", content=""):
        self.text = text
        self.content = content


# ------------------------------------------------------------- selection
def test_langchain_is_the_default():
    assert config.HARNESS == LANGCHAIN


def test_the_configured_harness_is_the_one_built(monkeypatch):
    built = {}

    monkeypatch.setattr(config, "HARNESS", DEEPAGENTS)
    monkeypatch.setitem(
        harness._BUILDERS, DEEPAGENTS, lambda m, t, p: built.setdefault("which", "deep")
    )
    build_agent(object(), [], "prompt")
    assert built["which"] == "deep"


def test_an_unknown_harness_fails_loudly(monkeypatch):
    """Silently falling back would make a comparison meaningless."""
    monkeypatch.setattr(config, "HARNESS", "not-a-harness")
    with pytest.raises(RuntimeError, match="Unknown MRA_HARNESS"):
        build_agent(object(), [], "prompt")


def test_selecting_deepagents_without_it_installed_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(config, "HARNESS", DEEPAGENTS)
    try:
        import deepagents  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="pip install deepagents"):
            build_agent(object(), [], "prompt")


# ------------------------------------------------------- result extraction
def test_langchain_shaped_results_are_read():
    assert final_text({"messages": [Message(text="the answer")]}) == "the answer"


def test_content_is_used_when_text_is_absent():
    assert final_text({"messages": [Message(content="the answer")]}) == "the answer"


def test_dict_shaped_messages_are_read():
    assert final_text({"messages": [{"content": "the answer"}]}) == "the answer"


def test_a_bare_string_result_is_passed_through():
    assert final_text("the answer") == "the answer"


def test_an_unrecognised_shape_returns_empty_rather_than_raising():
    """Prose never reaches the guard, so failing to extract it is harmless."""
    assert final_text({"nothing": "useful"}) == ""
    assert final_text(None) == ""
    assert final_text({"messages": []}) == ""


# --------------------------------------------- the guarantee is harness-free
def test_the_log_records_calls_no_matter_who_makes_them():
    """Tools write to the log as a side effect of being CALLED.

    This is why the harness cannot affect a verdict: the record is produced
    by execution, not by the agent's report of execution.
    """
    log = VerificationLog()
    tools = {tool.__name__: tool for tool in make_tools(log)}

    # nothing resembling an agent here at all
    tools["check_primality"]("561 is prime", "561")

    assert len(log.checks) == 1
    assert guard.decide("Is 561 prime?", log.checks).status is S.FALSE


def test_a_harness_that_reports_a_verdict_in_prose_is_ignored():
    """The model may claim anything; with no recorded checks it is NOT VERIFIED."""
    verdict = guard.decide("Is 561 prime?", [])
    assert verdict.status is S.NOT_APPLICABLE
    assert not verdict.was_verified
