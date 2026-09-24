"""Round-tripping a formal statement against the textbook it came from.

The method is HERALD-AF's, from REAL-Prover (arXiv:2505.20613). The first
test is the one that matters: if the back-translator can see the textbook
wording, it paraphrases that instead of reading the Lean, and the whole check
silently becomes a test of whether the model can copy a sentence.
"""

import pytest

from eval import roundtrip
from eval.roundtrip import MATCH, MISMATCH, UNCLEAR, Assessment


class Model:
    """Replies in order, and records every prompt it was given."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else ""


class Broken:
    def __init__(self, fail_on: int = 1):
        self.calls = 0
        self.fail_on = fail_on

    def invoke(self, prompt):
        self.calls += 1
        if self.calls >= self.fail_on:
            raise RuntimeError("the provider said no")
        return "some description"


FORMAL = "theorem t (n : Nat) : n + 0 = n"
INFORMAL = "Show that adding zero to a natural number leaves it unchanged."


# ------------------------------------------------- the load-bearing constraint
def test_the_back_translator_is_never_shown_the_textbook_wording():
    """Otherwise it paraphrases the textbook rather than reading the Lean,
    and a match proves only that the model can copy."""
    model = Model("n plus zero equals n.", "VERDICT: match\nWHY: same claim.")
    roundtrip.assess("g", "refuted", FORMAL, INFORMAL, model)

    back_translation_prompt = model.prompts[0]
    assert FORMAL in back_translation_prompt
    assert INFORMAL not in back_translation_prompt
    assert "unchanged" not in back_translation_prompt


def test_the_judge_sees_both_but_never_saw_the_lean():
    """It compares two English sentences. Handing it the Lean as well would
    let it decide the formalisation is fine on its own authority."""
    model = Model("n plus zero equals n.", "VERDICT: match\nWHY: same claim.")
    roundtrip.assess("g", "refuted", FORMAL, INFORMAL, model)

    judge_prompt = model.prompts[1]
    assert INFORMAL in judge_prompt
    assert "n plus zero equals n." in judge_prompt
    assert FORMAL not in judge_prompt


def test_the_back_translator_is_told_not_to_repair_the_statement():
    """A description that quietly corrects a broken statement would make a
    broken statement look faithful, which is the failure this whole check
    exists to catch."""
    assert "do not repair it" in roundtrip.BACK_TRANSLATE.lower()


# ------------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    "reply, expected",
    [
        ("VERDICT: match\nWHY: they agree.", MATCH),
        ("VERDICT: mismatch\nWHY: a hypothesis was dropped.", MISMATCH),
        ("VERDICT: unclear\nWHY: too vague.", UNCLEAR),
        ("verdict: MATCH\nwhy: fine.", MATCH),
        ("  VERDICT :  mismatch \n  WHY : no.", MISMATCH),
    ],
)
def test_the_two_lines_are_read(reply, expected):
    assert roundtrip.parse_judgement(reply)[0] == expected


@pytest.mark.parametrize(
    "reply",
    ["", "I think they're the same!", "VERDICT: probably", "VERDICT:\nWHY:"],
)
def test_an_unreadable_reply_becomes_unclear_not_a_decision(reply):
    """An unparseable answer is an ABSENCE of evidence. Defaulting to either
    decisive value would invent evidence: one sends a human looking for
    nothing, the other stops them looking at something real."""
    verdict, _ = roundtrip.parse_judgement(reply)
    assert verdict == UNCLEAR


# -------------------------------------------------------------- the assessment
def test_a_faithful_statement_comes_back_as_a_match():
    model = Model("n plus zero is n.", "VERDICT: match\nWHY: identical.")
    result = roundtrip.assess("g", "suspect_statement", FORMAL, INFORMAL, model)
    assert result.verdict == MATCH
    assert result.why == "identical."
    assert result.back_translation == "n plus zero is n."


def test_a_divergent_statement_comes_back_as_a_mismatch():
    model = Model("Every group is abelian.",
                  "VERDICT: mismatch\nWHY: a different claim entirely.")
    result = roundtrip.assess("g", "suspect_statement", FORMAL, INFORMAL, model)
    assert result.verdict == MISMATCH
    assert not result.needs_a_human


def test_a_message_object_is_unwrapped():
    """LangChain returns objects with .content, not bare strings."""
    class Message:
        def __init__(self, content):
            self.content = content

    class Wrapping(Model):
        def invoke(self, prompt):
            return Message(super().invoke(prompt))

    model = Wrapping("n plus zero is n.", "VERDICT: match\nWHY: same.")
    assert roundtrip.assess("g", "refuted", FORMAL, INFORMAL, model).verdict == MATCH


# ------------------------------------------------------ never raises, never guesses
def test_a_provider_failure_is_unclear_and_is_reported():
    result = roundtrip.assess("g", "refuted", FORMAL, INFORMAL, Broken(fail_on=1))
    assert result.verdict == UNCLEAR
    assert "back-translation failed" in result.why


def test_a_failure_while_judging_keeps_what_was_already_learned():
    result = roundtrip.assess("g", "refuted", FORMAL, INFORMAL, Broken(fail_on=2))
    assert result.verdict == UNCLEAR
    assert "judging failed" in result.why
    assert result.back_translation == "some description"


@pytest.mark.parametrize(
    "formal, informal",
    [("", INFORMAL), (FORMAL, ""), ("", ""), ("   ", INFORMAL)],
)
def test_a_missing_side_is_not_a_verdict(formal, informal):
    model = Model("anything", "VERDICT: match\nWHY: sure.")
    result = roundtrip.assess("g", "refuted", formal, informal, model)
    assert result.verdict == UNCLEAR
    assert not model.prompts, "nothing should have been sent"


def test_an_empty_back_translation_is_not_judged():
    model = Model("", "VERDICT: match\nWHY: sure.")
    result = roundtrip.assess("g", "refuted", FORMAL, INFORMAL, model)
    assert result.verdict == UNCLEAR
    assert len(model.prompts) == 1


# ------------------------------------------------------------------ triage
def test_a_match_on_an_excluded_goal_is_what_a_human_should_open():
    """The agent called it broken; an independent reading says it is
    faithful. That disagreement is the finding."""
    assert Assessment("g", "suspect_statement", MATCH, "").needs_a_human
    assert Assessment("g", "refuted", MATCH, "").needs_a_human
    assert not Assessment("g", "suspect_statement", MISMATCH, "").needs_a_human
    assert not Assessment("g", "suspect_statement", UNCLEAR, "").needs_a_human


def test_the_summary_separates_the_outcomes():
    assessments = [
        Assessment("a", "suspect_statement", MATCH, ""),
        Assessment("b", "suspect_statement", MISMATCH, ""),
        Assessment("c", "refuted", MATCH, ""),
        Assessment("d", "refuted", UNCLEAR, ""),
    ]
    summary = roundtrip.summarize(assessments)
    assert summary["assessed"] == 4
    assert summary["needs_a_human"] == 2
    assert summary["by_outcome_and_verdict"]["suspect_statement/match"] == 1
    assert summary["by_outcome_and_verdict"]["refuted/unclear"] == 1


def test_only_the_unaudited_outcomes_are_targeted_by_default():
    """Proved and not_proved goals were tested as written, so their
    statements are not in question."""
    assert set(roundtrip.AUDITABLE) == {"suspect_statement", "refuted"}
