"""The instrument that says whether a difference is real.

Statistics code is the worst place for a plausible-looking bug, because its
output is a number that settles arguments and nothing downstream can tell a
right one from a wrong one. So the tests here check against values computed
by hand from the definition, not against whatever the code happens to print.
"""

import json

import pytest

from eval import variance
from eval.variance import Run


def run(name: str, **outcomes: str) -> Run:
    return Run(name=name, outcomes=dict(outcomes))


def proving(name: str, proved: list[str], failed: list[str] = ()) -> Run:
    outcomes = {goal: "proved" for goal in proved}
    outcomes.update({goal: "not_proved" for goal in failed})
    return Run(name=name, outcomes=outcomes)


# ------------------------------------------------------------ what counts
def test_a_goal_the_mathematics_refused_is_not_a_fair_test():
    """Kept in step with proof_metrics' valid_targets, and for its reasons:
    a refuted statement had no proof to find."""
    sample = run(
        "r",
        a="proved", b="not_proved", c="refuted",
        d="suspect_statement", e="not_formalized", f="exhausted",
    )
    assert sample.valid == {"a", "b"}


def test_comparison_is_restricted_to_goals_valid_in_every_run():
    """A goal refuted in one run and attempted in another is not the same
    measurement twice. Averaging over the union compares two denominators."""
    first = run("one", a="proved", b="not_proved", c="proved")
    second = run("two", a="not_proved", b="refuted", c="proved")
    assert variance.common_goals([first, second]) == {"a", "c"}


def test_runs_with_nothing_in_common_yield_nothing():
    assert variance.common_goals(
        [run("one", a="proved"), run("two", b="proved")]
    ) == set()


# --------------------------------------------------------------- the noise
def test_a_flipper_is_a_goal_that_did_not_answer_the_same_way_twice():
    goals = {"stable_yes", "stable_no", "flipper"}
    first = proving("one", ["stable_yes", "flipper"], ["stable_no"])
    second = proving("two", ["stable_yes"], ["stable_no", "flipper"])
    assert variance.flippers([first, second], goals) == ["flipper"]


def test_pass_at_k_counts_a_goal_proved_by_any_run():
    goals = {"a", "b", "c"}
    first = proving("one", ["a"], ["b", "c"])
    second = proving("two", ["b"], ["a", "c"])
    assert variance.pass_at_k([first, second], goals) == pytest.approx(2 / 3)
    assert variance.rate(first, goals) == pytest.approx(1 / 3)


def test_success_probability_is_per_goal_over_runs():
    goals = {"always", "never", "half"}
    first = proving("one", ["always", "half"], ["never"])
    second = proving("two", ["always"], ["never", "half"])
    assert variance.success_probability([first, second], goals) == {
        "always": 1.0, "never": 0.0, "half": 0.5,
    }


# ------------------------------------------------------- McNemar, by hand
def test_mcnemar_counts_the_four_cells():
    goals = {"both", "only_a", "only_b", "neither"}
    a = proving("a", ["both", "only_a"], ["only_b", "neither"])
    b = proving("b", ["both", "only_b"], ["only_a", "neither"])
    result = variance.mcnemar(a, b, goals)
    assert (result.both, result.only_a, result.only_b, result.neither) == (1, 1, 1, 1)


def test_five_nil_gives_the_p_value_the_definition_gives():
    """b + c = 5 discordant, all one way.

    P(X >= 5) for X ~ Binomial(5, 1/2) is 1/32 = 0.03125.
    Two-sided doubles it: 0.0625.
    """
    goals = {f"g{i}" for i in range(10)}
    a = proving("a", [], sorted(goals))
    b = proving("b", [f"g{i}" for i in range(5)],
                [f"g{i}" for i in range(5, 10)])
    result = variance.mcnemar(a, b, goals)
    assert result.discordant == 5
    assert result.p_value == pytest.approx(0.0625)
    assert not result.distinguishable


def test_ten_nil_clears_the_bar():
    """P(X >= 10) for Binomial(10, 1/2) is 1/1024; doubled, 0.00195."""
    goals = {f"g{i}" for i in range(10)}
    a = proving("a", [], sorted(goals))
    b = proving("b", sorted(goals), [])
    result = variance.mcnemar(a, b, goals)
    assert result.p_value == pytest.approx(2 / 1024)
    assert result.distinguishable


def test_total_agreement_is_not_evidence_of_anything():
    goals = {"a", "b"}
    same = proving("one", ["a"], ["b"])
    other = proving("two", ["a"], ["b"])
    result = variance.mcnemar(same, other, goals)
    assert result.discordant == 0
    assert result.p_value == 1.0


def test_concordant_goals_do_not_change_the_p_value():
    """THE reason to pair. Goals both runs agree on carry no information
    about which is better, and an unpaired test dilutes the signal with
    them."""
    small = {f"g{i}" for i in range(6)}
    a_small = proving("a", [], sorted(small))
    b_small = proving("b", [f"g{i}" for i in range(5)], ["g5"])

    large = {f"g{i}" for i in range(600)}
    a_large = proving("a", [], sorted(large))
    b_large = proving(
        "b", [f"g{i}" for i in range(5)], [f"g{i}" for i in range(5, 600)]
    )
    assert variance.mcnemar(a_small, b_small, small).p_value == pytest.approx(
        variance.mcnemar(a_large, b_large, large).p_value
    )


def test_it_says_when_a_comparison_was_too_small_to_settle_anything():
    """'Not significant' is read as 'no difference' unless the alternative
    is spelled out."""
    assert "COULD NOT" in variance.detectable_difference(5)
    assert "nothing to test" in variance.detectable_difference(0)
    assert "capable of detecting" in variance.detectable_difference(12)


# ------------------------------------------------------------- the interval
def test_the_bootstrap_is_reproducible():
    """A bootstrap that answers differently each time is a second moving
    ruler."""
    goals = {f"g{i}" for i in range(20)}
    a = [proving("a", [f"g{i}" for i in range(12)],
                 [f"g{i}" for i in range(12, 20)])]
    b = [proving("b", [f"g{i}" for i in range(8)],
                 [f"g{i}" for i in range(8, 20)])]
    first = variance.paired_difference(a, b, goals, iterations=2000)
    second = variance.paired_difference(a, b, goals, iterations=2000)
    assert (first.point, first.low, first.high) == (
        second.point, second.low, second.high
    )


def test_the_paired_difference_has_the_sign_of_the_better_side():
    goals = {f"g{i}" for i in range(20)}
    better = [proving("a", [f"g{i}" for i in range(15)],
                      [f"g{i}" for i in range(15, 20)])]
    worse = [proving("b", [f"g{i}" for i in range(5)],
                     [f"g{i}" for i in range(5, 20)])]
    difference = variance.paired_difference(better, worse, goals, iterations=2000)
    assert difference.point == pytest.approx(0.5)
    assert not difference.includes_zero


def test_identical_sides_cannot_be_told_apart():
    goals = {f"g{i}" for i in range(20)}
    one = [proving("a", [f"g{i}" for i in range(10)],
                   [f"g{i}" for i in range(10, 20)])]
    two = [proving("b", [f"g{i}" for i in range(10)],
                   [f"g{i}" for i in range(10, 20)])]
    difference = variance.paired_difference(one, two, goals, iterations=2000)
    assert difference.point == 0.0
    assert difference.includes_zero


def test_empty_input_does_not_raise():
    assert variance.paired_difference([], [], set()).point == 0.0
    assert variance.rate_interval([], set()).point == 0.0
    assert variance.pass_at_k([], set()) == 0.0


# ------------------------------------------------------------------ loading
def test_a_results_file_loads_by_goal_id(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({
        "summary": {},
        "results": [
            {"goal_id": "a", "outcome": "proved"},
            {"goal_id": "b", "outcome": "refuted"},
        ],
    }))
    loaded = variance.load_run(path)
    assert loaded.outcomes == {"a": "proved", "b": "refuted"}
    assert loaded.valid == {"a"}


def test_a_bare_list_of_rows_also_loads(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps([{"goal_id": "a", "outcome": "proved"}]))
    assert variance.load_run(path).outcomes == {"a": "proved"}


def test_the_verification_results_shape_loads_too(tmp_path):
    """eval/evaluate.py writes case_id rather than goal_id."""
    path = tmp_path / "run.json"
    path.write_text(json.dumps({
        "results": [{"case_id": "calc-1", "outcome": "correct"}]
    }))
    assert variance.load_run(path).outcomes == {"calc-1": "correct"}
