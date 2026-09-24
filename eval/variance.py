"""Is that difference real, or is it the run-to-run noise?

WHY THIS EXISTS. A ~16% goal-level flip rate was measured on this system:
run the same goals twice with nothing changed and about one in six goals
changes its answer. A single-run proof rate is therefore not an instrument.
Three hypotheses have already been withdrawn after being "supported" by
comparisons that a moving ruler cannot support, and every future capability
change faces the same problem.

THE CENTRAL POINT, and the reason this file is not two lines of arithmetic:

    Comparing two runs as independent proportions throws away the pairing.

The same goals appear in both runs. Goal difficulty is the largest source of
variance in a proof rate and it is IDENTICAL on both sides, so pairing
removes it outright. A paired test on 60 goals has more power than an
unpaired test on several hundred, at no extra cost, from data already on
disk.

TWO TESTS, for two situations:

  McNemar (exact)   two SINGLE runs, A and B. Looks only at the goals where
                    the runs disagree: b proved in A not B, c proved in B not
                    A. Under the hypothesis that the change did nothing, each
                    disagreement is a coin flip, so b ~ Binomial(b + c, 1/2)
                    and the p-value is exact — no normal approximation, which
                    matters because b + c is usually small.

                    This is the test for the comparisons already being made.

  Paired bootstrap  several runs per side. Estimates each goal's success
                    PROBABILITY on each side, then resamples GOALS (not runs)
                    to put an interval on the mean paired difference.
                    Resampling goals is what makes the interval mean
                    "if I had drawn a different sample of goals".

WHAT NEITHER CAN DO. Neither tells you a difference matters, only whether it
is distinguishable from noise. And an interval that contains zero is not
evidence of no effect — it is the absence of evidence of one, which on two
runs of sixty goals is the usual outcome for any real but modest change.
The report says so rather than leaving it to be misread.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

# What counts as a success, for BOTH kinds of run this reads.
#
# A proving run records `proved`; a verification run records `correct`.
# Neither vocabulary uses the other's word, so the union is unambiguous and
# one instrument serves both. Without this the tool silently reported 0% on
# every verification run -- an answer that looks like a measurement.
SUCCEEDED = frozenset({"proved", "correct"})
PROVED = "proved"

# Outcomes that mean "this goal was NOT a fair test". Kept in step with
# eval/proof_metrics.py's `valid_targets`, and for the same reasons: a
# statement the compiler refuted, one the agent called broken, one that never
# formalised and one that ran out of budget are all excluded, because none of
# them was refused by the mathematics.
EXCLUDED = frozenset({"refuted", "suspect_statement", "not_formalized", "exhausted"})


@dataclass(frozen=True)
class Run:
    """One results file, reduced to what a comparison needs."""

    name: str
    outcomes: dict[str, str]

    @property
    def valid(self) -> set[str]:
        return {
            goal for goal, outcome in self.outcomes.items()
            if outcome not in EXCLUDED
        }

    def proved(self, goal: str) -> bool:
        return self.outcomes.get(goal) in SUCCEEDED


def load_run(path: str | Path) -> Run:
    """Read a results file into a Run."""
    path = Path(path)
    data = json.loads(path.read_text())
    rows = data["results"] if isinstance(data, dict) and "results" in data else data
    outcomes = {}
    for row in rows:
        goal = row.get("goal_id") or row.get("case_id") or row.get("id")
        if goal is not None:
            outcomes[str(goal)] = str(row.get("outcome", ""))
    return Run(name=path.stem, outcomes=outcomes)


def common_goals(runs: list[Run]) -> set[str]:
    """Goals that are a fair test in EVERY run being compared.

    Restricting to the intersection is what makes the comparison paired. A
    goal refuted in one run and attempted in another is not the same
    measurement twice, and averaging over the union would quietly compare
    different denominators.
    """
    if not runs:
        return set()
    shared = set(runs[0].valid)
    for run in runs[1:]:
        shared &= run.valid
    return shared


# ------------------------------------------------------------------ one side
def rate(run: Run, goals: set[str]) -> float:
    if not goals:
        return 0.0
    return sum(run.proved(goal) for goal in goals) / len(goals)


def success_probability(runs: list[Run], goals: set[str]) -> dict[str, float]:
    """Each goal's estimated chance of being proved, over these runs."""
    return {
        goal: sum(run.proved(goal) for run in runs) / len(runs)
        for goal in goals
    }


def flippers(runs: list[Run], goals: set[str]) -> list[str]:
    """Goals that did not give the same answer every time.

    This is the instrument's noise, stated directly rather than inferred
    from a difference in two summary numbers.
    """
    return sorted(
        goal for goal in goals
        if 0 < sum(run.proved(goal) for run in runs) < len(runs)
    )


def pass_at_k(runs: list[Run], goals: set[str]) -> float:
    """Fraction of goals proved by AT LEAST ONE of these runs."""
    if not goals:
        return 0.0
    return sum(
        any(run.proved(goal) for run in runs) for goal in goals
    ) / len(goals)


# ------------------------------------------------------------ McNemar, exact
def _binomial_tail(successes: int, trials: int) -> float:
    """P(X >= successes) for X ~ Binomial(trials, 1/2)."""
    from math import comb

    return sum(comb(trials, i) for i in range(successes, trials + 1)) / (2 ** trials)


@dataclass(frozen=True)
class McNemar:
    only_a: int          # proved in A, not in B
    only_b: int          # proved in B, not in A
    both: int
    neither: int
    p_value: float

    @property
    def discordant(self) -> int:
        return self.only_a + self.only_b

    @property
    def distinguishable(self) -> bool:
        return self.p_value < 0.05


def mcnemar(run_a: Run, run_b: Run, goals: set[str]) -> McNemar:
    """Exact paired test for two single runs.

    Only the goals where the runs DISAGREE carry information. A goal proved
    by both, or by neither, says nothing about which run is better, and
    including it in the denominator is what makes an unpaired comparison so
    much weaker than it needs to be.
    """
    only_a = only_b = both = neither = 0
    for goal in goals:
        a, b = run_a.proved(goal), run_b.proved(goal)
        if a and b:
            both += 1
        elif a:
            only_a += 1
        elif b:
            only_b += 1
        else:
            neither += 1

    discordant = only_a + only_b
    if discordant == 0:
        p_value = 1.0
    else:
        larger = max(only_a, only_b)
        # Two-sided: double the tail, capped at 1.
        p_value = min(1.0, 2.0 * _binomial_tail(larger, discordant))

    return McNemar(only_a, only_b, both, neither, p_value)


# -------------------------------------------------------- paired bootstrap
@dataclass(frozen=True)
class Interval:
    point: float
    low: float
    high: float

    @property
    def includes_zero(self) -> bool:
        return self.low <= 0.0 <= self.high


def paired_difference(
    group_a: list[Run],
    group_b: list[Run],
    goals: set[str],
    iterations: int = 10000,
    seed: int = 20260924,
) -> Interval:
    """Mean per-goal difference in success probability, with a 95% interval.

    GOALS are resampled, not runs. The interval then answers "what if I had
    drawn a different sample of goals", which is the question a benchmark
    result is implicitly making a claim about.

    The seed is fixed so that a report is reproducible; a bootstrap that
    gives a different answer each time is a second moving ruler.
    """
    if not goals:
        return Interval(0.0, 0.0, 0.0)

    probability_a = success_probability(group_a, goals)
    probability_b = success_probability(group_b, goals)
    ordered = sorted(goals)
    differences = [probability_a[goal] - probability_b[goal] for goal in ordered]

    point = sum(differences) / len(differences)

    rng = random.Random(seed)
    size = len(differences)
    means = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(size):
            total += differences[rng.randrange(size)]
        means.append(total / size)
    means.sort()

    low = means[int(0.025 * iterations)]
    high = means[min(int(0.975 * iterations), iterations - 1)]
    return Interval(point, low, high)


def rate_interval(
    runs: list[Run],
    goals: set[str],
    iterations: int = 10000,
    seed: int = 20260924,
) -> Interval:
    """The single-run proof rate, with a 95% interval over goals."""
    if not goals:
        return Interval(0.0, 0.0, 0.0)

    probability = success_probability(runs, goals)
    ordered = sorted(goals)
    values = [probability[goal] for goal in ordered]
    point = sum(values) / len(values)

    rng = random.Random(seed)
    size = len(values)
    means = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(size):
            total += values[rng.randrange(size)]
        means.append(total / size)
    means.sort()

    return Interval(
        point,
        means[int(0.025 * iterations)],
        means[min(int(0.975 * iterations), iterations - 1)],
    )


def detectable_difference(discordant: int) -> str:
    """Plain words about what a comparison of this size could have found.

    Stated because "no significant difference" is read as "no difference"
    unless the alternative is spelled out. With five disagreeing goals, an
    exact paired test cannot reach p < 0.05 however they split: the smallest
    attainable two-sided p-value is 2 * (1/2)^5 = 0.0625.
    """
    if discordant == 0:
        return (
            "The two runs agreed on every goal, so there is nothing to test. "
            "Either the change did nothing, or it does nothing to these goals."
        )
    smallest = min(1.0, 2.0 * _binomial_tail(discordant, discordant))
    if smallest >= 0.05:
        return (
            f"Only {discordant} goal(s) disagreed. Even a unanimous split "
            f"gives p = {smallest:.3f}, so this comparison COULD NOT have "
            "reached significance whatever happened. It is too small to "
            "settle anything; the answer is more goals or more runs, not a "
            "reading of these numbers."
        )
    return (
        f"{discordant} goal(s) disagreed, so a unanimous split would have "
        f"given p = {smallest:.3f}. The comparison was capable of detecting "
        "a large effect."
    )
