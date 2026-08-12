"""Offline tests for `try_lemma` and `try_skeleton`. No model, no Lean.

`test_proving_a_lemma_does_not_prove_the_goal` is the load-bearing one. A run
that proves five helper lemmas and never closes the goal has proved nothing
about the goal, and the guard must not read one as the other.
"""

import config
from domain.verdict import Verdict, VerificationStatus as S
from pipeline.proof_tools import ProofLog, make_proof_tools
from verifiers.lean_verifier import build_source, rename_goal

ACCEPTED = Verdict(S.TRUE, "lean", "accepted")
REJECTED = Verdict(S.UNKNOWN, "lean", "error: no")


def accepts(statement, proof):
    return ACCEPTED


def rejects(statement, proof):
    return REJECTED


def tools_for(check=accepts, structure_check=None, statement="theorem g : True"):
    log = ProofLog(statement=statement)
    made = make_proof_tools(log, check, None, structure_check)
    return log, {tool.__name__: tool for tool in made}


# ------------------------------------------------------------ the guard
def test_proving_a_lemma_does_not_prove_the_goal():
    """THE constraint. A helper is progress; it is not the goal."""
    log, tools = tools_for(check=accepts)

    tools["try_lemma"]("lemma helper : True", "trivial")

    assert log.lemmas, "the lemma should have been kept"
    assert log.accepted is None, "a lemma's success was read as the goal's"
    assert log.attempts == [], "a lemma was recorded as an attempt at the goal"


def test_a_skeleton_never_counts_as_a_proof():
    """It compiles with `sorry`, which proves nothing."""
    log, tools = tools_for(check=accepts, structure_check=lambda s, p: True)

    tools["try_skeleton"]("have h : True := by sorry\nexact h")

    assert log.accepted is None, "a skeleton was read as a proof"
    assert log.attempts, "the skeleton should still be recorded"


# ------------------------------------------------------- keeping and citing
def test_a_kept_lemma_is_visible_to_later_attempts():
    seen = []

    def check(statement, proof):
        seen.append(statement)
        return ACCEPTED

    log, tools = tools_for(check=check, statement="theorem g : True")
    tools["try_lemma"]("lemma helper : True", "trivial")
    tools["try_proof"]("exact helper")

    assert "helper" in seen[-1], "the goal was compiled without the kept lemma"
    assert "theorem g : True" in seen[-1]


def test_a_rejected_lemma_is_not_kept():
    log, tools = tools_for(check=rejects)
    reply = tools["try_lemma"]("lemma helper : False", "trivial")

    assert log.lemmas == []
    assert "REJECTED" in reply


def test_a_lemma_may_build_on_an_earlier_lemma():
    seen = []

    def check(statement, proof):
        seen.append(statement)
        return ACCEPTED

    _, tools = tools_for(check=check)
    tools["try_lemma"]("lemma first : True", "trivial")
    tools["try_lemma"]("lemma second : True", "exact first")

    assert "first" in seen[-1], "the second lemma could not see the first"


def test_the_number_of_kept_lemmas_is_bounded(monkeypatch):
    """Each one grows the file every later attempt must recompile."""
    monkeypatch.setattr(config, "MAX_AGENT_LEMMAS", 2)
    log, tools = tools_for(check=accepts)

    for index in range(4):
        tools["try_lemma"](f"lemma h{index} : True", "trivial")

    assert len(log.lemmas) == 2


# ------------------------------------------------------------- the skeleton
def test_a_skeleton_that_typechecks_reports_what_is_left():
    _, tools = tools_for(structure_check=lambda s, p: True)

    reply = tools["try_skeleton"](
        "have h1 : 1 = 1 := by sorry\nhave h2 : 2 = 2 := by sorry\nexact h1"
    )

    assert "TYPECHECKS" in reply
    assert "1 = 1" in reply and "2 = 2" in reply


def test_a_skeleton_that_does_not_typecheck_says_so():
    _, tools = tools_for(structure_check=lambda s, p: False)
    reply = tools["try_skeleton"]("have h : True := by sorry")

    assert "does NOT typecheck" in reply


def test_the_skeleton_tool_is_absent_when_it_cannot_be_checked():
    _, tools = tools_for(structure_check=None)
    assert "try_skeleton" not in tools


# --------------------------------------------------- naming, in a multi-decl file
def test_only_the_goal_is_renamed_when_lemmas_precede_it():
    """The lemmas keep their names because the proof cites them."""
    source = build_source(
        "lemma helper : True := trivial\n\ntheorem irrational_sqrt_two : True",
        "exact helper",
    )
    assert "lemma helper" in source, "a kept lemma was renamed out from under the proof"
    assert "theorem mra_goal" in source


def test_renaming_is_unchanged_for_a_single_declaration():
    assert rename_goal("theorem foo : True") == "theorem mra_goal : True"


# ------------------------------------------------------------------ budget
def test_lemmas_and_skeletons_are_charged_as_compilations():
    log, tools = tools_for(check=accepts, structure_check=lambda s, p: True)

    tools["try_lemma"]("lemma h : True", "trivial")
    tools["try_skeleton"]("have h : True := by sorry")

    assert log.budget.lean_calls == 2, "an expensive tool was not charged"
