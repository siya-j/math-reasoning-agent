"""Offline tests for the scoring logic.

The scoring rules are what tell us whether the agent is sound, so they get
tested harder than the agent does.
"""

from domain.verdict import VerificationStatus as S
from eval.dataset import load_cases
from eval.metrics import CaseResult, Outcome, classify, summarize


# ------------------------------------------------------------- classify
def test_matching_verdict_is_correct():
    assert classify(S.TRUE, S.TRUE) is Outcome.CORRECT
    assert classify(S.FALSE, S.FALSE) is Outcome.CORRECT


def test_opposite_verdict_is_wrong():
    assert classify(S.TRUE, S.FALSE) is Outcome.WRONG
    assert classify(S.FALSE, S.TRUE) is Outcome.WRONG


def test_undecided_on_a_decidable_case_is_only_missed():
    """A coverage gap, not a soundness failure."""
    assert classify(S.TRUE, S.UNKNOWN) is Outcome.MISSED
    assert classify(S.FALSE, S.NOT_APPLICABLE) is Outcome.MISSED


def test_claiming_verification_for_the_unverifiable_is_wrong():
    """The failure mode this whole architecture exists to prevent."""
    assert classify(S.NOT_APPLICABLE, S.TRUE) is Outcome.WRONG
    assert classify(S.NOT_APPLICABLE, S.FALSE) is Outcome.WRONG


def test_honest_refusal_on_an_undecidable_case_is_correct():
    assert classify(S.NOT_APPLICABLE, S.NOT_APPLICABLE) is Outcome.CORRECT
    assert classify(S.NOT_APPLICABLE, S.UNKNOWN) is Outcome.CORRECT


# ------------------------------------------------------------- summarize
def _result(outcome, expected="true", checks=1):
    return CaseResult(
        case_id="c",
        area="a",
        expected=expected,
        actual="true",
        outcome=outcome,
        checks=checks,
    )


def test_soundness_is_one_when_nothing_is_wrong():
    summary = summarize([_result(Outcome.CORRECT), _result(Outcome.MISSED)])
    assert summary["soundness"] == 1.0
    assert summary["accuracy"] == 0.5


def test_soundness_drops_when_a_case_is_wrong():
    summary = summarize([_result(Outcome.CORRECT), _result(Outcome.WRONG)])
    assert summary["soundness"] == 0.5


def test_coverage_only_counts_decidable_cases():
    results = [
        _result(Outcome.CORRECT, expected="true"),
        _result(Outcome.MISSED, expected="true"),
        _result(Outcome.CORRECT, expected="n/a"),
    ]
    assert summarize(results)["coverage"] == 0.5


def test_tool_use_rate_detects_answering_from_memory():
    results = [_result(Outcome.CORRECT, checks=2), _result(Outcome.CORRECT, checks=0)]
    assert summarize(results)["tool_use_rate"] == 0.5


def test_restraint_measures_not_verifying_the_unverifiable():
    results = [
        _result(Outcome.CORRECT, expected="n/a", checks=0),
        _result(Outcome.WRONG, expected="n/a", checks=3),
    ]
    assert summarize(results)["restraint_on_abstract"] == 0.5


# --------------------------------------------------------------- dataset
def test_golden_dataset_loads_and_is_well_formed():
    cases = load_cases()
    assert len(cases) >= 80
    assert len({c.id for c in cases}) == len(cases), "duplicate case ids"
    assert all(c.question.strip() for c in cases)


def test_dataset_covers_every_area_the_tools_support():
    areas = {c.area for c in load_cases()}
    for area in ("calculus", "limits", "arithmetic", "number theory", "algebra"):
        assert area in areas, f"no cases for {area}"


def test_dataset_covers_every_expected_outcome():
    expectations = {c.expected for c in load_cases()}
    assert S.TRUE in expectations
    assert S.FALSE in expectations
    assert S.NOT_APPLICABLE in expectations


def test_dataset_includes_abstract_cases_no_cas_can_decide():
    abstract = [c for c in load_cases() if c.area == "abstract"]
    assert len(abstract) >= 10
    assert all(c.expected is S.NOT_APPLICABLE for c in abstract)


def test_dataset_has_enough_false_cases_to_catch_a_yes_machine():
    """A system that always answers 'true' must score badly, not well."""
    false_cases = [c for c in load_cases() if c.expected is S.FALSE]
    assert len(false_cases) >= 15
