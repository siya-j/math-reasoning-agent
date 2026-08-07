"""Offline tests for the pipeline's outer loop (Design Doc Phases 4 and 5).

The agent node is stubbed; the guard, the retry policy and the real SymPy
verifier are not. These tests exist because when this loop lived inside the
agent, a small model simply chose not to iterate — so the capability must be
proven to live in code, not in the model's judgment.
"""

import config
import pipeline.pipeline as pipeline_module
from domain.attempt import Strategy
from domain.verdict import VerificationStatus as S
from pipeline.tools import VerificationLog, make_tools

GOOD = ("check_equality", "the derivative of x^3 is 3x^2", "diff(x**3, x)", "3*x**2")
WRONG = ("check_numeric", "2 + 2 equals 5", "2 + 2", "5")
BROKEN = ("check_equality", "malformed", "))((", "1")


class FakeAgent:
    """Replays a scripted sequence of tool-call plans, one per invocation."""

    def __init__(self, plans):
        self.plans = list(plans)
        self.instructions = []

    def __call__(self, model, question, extra_instruction=""):
        self.instructions.append(extra_instruction)
        plan = self.plans.pop(0) if self.plans else []
        log = VerificationLog()
        equality, numeric, _, _, _ = make_tools(log)
        for tool, claim, lhs, rhs in plan:
            if tool == "check_equality":
                equality(claim, lhs, rhs)
            else:
                numeric(claim, lhs, rhs)
        return log.checks, "model prose"


def run_with(plans, question="Is the derivative of x^3 equal to 3x^2?"):
    fake = FakeAgent(plans)
    original = pipeline_module.invoke_once
    pipeline_module.invoke_once = fake
    try:
        return pipeline_module.run(question, model=object()), fake
    finally:
        pipeline_module.invoke_once = original


# ------------------------------------------------------ Phase 4: reflection
def test_success_on_the_first_pass_makes_one_attempt():
    state, _ = run_with([[GOOD]])
    assert len(state.attempts) == 1
    assert state.attempts[0].strategy is Strategy.INITIAL
    assert state.verdict.status is S.TRUE


def test_a_false_verdict_is_never_retried():
    """Retrying until the verifier agrees would be an agreement machine."""
    state, _ = run_with([[WRONG]])
    assert len(state.attempts) == 1
    assert state.verdict.status is S.FALSE


def test_an_undecidable_check_is_retried_by_the_pipeline():
    """The capability must not depend on the model choosing to iterate."""
    state, fake = run_with([[BROKEN], [GOOD]])
    assert len(state.attempts) == 2
    assert state.attempts[1].strategy is Strategy.RETRY_MALFORMED
    assert state.verdict.status is S.TRUE
    assert "could not be decided" in fake.instructions[1]


def test_retries_are_bounded_and_give_up_honestly():
    state, _ = run_with([[BROKEN]] * 5)
    assert len(state.attempts) == config.MAX_ATTEMPTS
    assert state.verdict.status is S.UNKNOWN
    assert not state.verdict.was_verified


def test_calling_no_tools_is_nudged_exactly_once():
    """One nudge distinguishes 'forgot' from 'genuinely uncheckable'."""
    state, fake = run_with([[], [], []])
    strategies = [a.strategy for a in state.attempts]
    assert strategies == [Strategy.INITIAL, Strategy.RETRY_NO_TOOLS]
    assert "nothing was verified" in fake.instructions[1]
    assert state.verdict.status is S.NOT_APPLICABLE


def test_the_nudge_can_recover_a_missed_verification():
    state, _ = run_with([[], [GOOD]])
    assert len(state.attempts) == 2
    assert state.verdict.status is S.TRUE


# --------------------------------------------------- Phase 5: decomposition
def test_evidence_is_gathered_when_the_claim_is_unverified():
    state, fake = run_with([[], [], [GOOD]])
    assert len(state.evidence) == 1
    assert "AUXILIARY" in fake.instructions[-1]


def test_evidence_never_changes_the_verdict():
    """Verified special cases do not establish a general claim."""
    state, _ = run_with([[], [], [GOOD]])
    assert all(c.verdict.status is S.TRUE for c in state.evidence)
    assert state.verdict.status is S.NOT_APPLICABLE
    assert not state.verdict.was_verified


def test_no_decomposition_when_the_claim_was_verified():
    state, _ = run_with([[GOOD]])
    assert state.evidence == []


# ------------------------------------------------- Principle 5: explicit state
def test_every_attempt_is_recorded_for_inspection():
    state, _ = run_with([[BROKEN], [GOOD]])
    assert [a.number for a in state.attempts] == [1, 2]
    assert len(state.all_checks) == 2
    assert any("reflect" in entry for entry in state.trace)
