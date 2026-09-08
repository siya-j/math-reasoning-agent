"""A partial run must not systematically sample the easy goals.

MEASURED. eval/mixed-benchmark.json is ordered easy to hard -- in-mathlib and
near-mathlib at positions 1-13, hard and deep at 16-25, proofnet at 26-45,
putnam at 46-65 -- and a run was interrupted at position 33. It had therefore
completed every self-authored tier and only seven of twenty ProofNet goals, so
its 75% proof rate is an UPPER BOUND, not an estimate: the 33 goals remaining
were the two hardest blocks, currently scoring 29% and 40%.

The same ordering makes cost look like it is degrading mid-run. Model calls
per goal climb from ~7.6 on the in-mathlib block to ~18 on ProofNet and ~24 on
Putnam, purely because the goals get harder.
"""

import random

from scripts.evaluate_proofs import DEFAULT_SEED


class Goal:
    def __init__(self, index, tier):
        self.id, self.tier = f"g{index}", tier

    def __repr__(self):
        return f"{self.id}/{self.tier}"


def _ordered_like_the_benchmark():
    """Easy first, hard last -- the shape of the real file."""
    tiers = (["easy"] * 13 + ["novel"] * 2 + ["hard"] * 5 + ["deep"] * 5
             + ["proofnet"] * 20 + ["putnam"] * 20)
    return [Goal(i, t) for i, t in enumerate(tiers)]


def test_the_unshuffled_order_is_biased_and_this_is_why_the_flag_exists():
    """Not a test of the flag -- a test of the premise. The first third of the
    file contains no hard goal at all, so a run stopped there cannot have
    sampled one."""
    goals = _ordered_like_the_benchmark()
    first_third = goals[: len(goals) // 3]
    hard_tiers = {"proofnet", "putnam"}

    assert not [g for g in first_third if g.tier in hard_tiers], (
        "the premise no longer holds; the benchmark may have been reordered"
    )


def test_a_seeded_shuffle_samples_across_tiers():
    goals = _ordered_like_the_benchmark()
    random.Random(DEFAULT_SEED).shuffle(goals)

    first_third = goals[: len(goals) // 3]
    tiers = {g.tier for g in first_third}
    assert len(tiers) >= 4, tiers
    assert {"proofnet", "putnam"} & tiers, (
        "a shuffled partial run still missed both external tiers"
    )


def test_the_same_seed_gives_the_same_order():
    """Required for `--resume`: a different order between runs would make the
    remaining set depend on when the interruption happened."""
    a, b = _ordered_like_the_benchmark(), _ordered_like_the_benchmark()
    random.Random(DEFAULT_SEED).shuffle(a)
    random.Random(DEFAULT_SEED).shuffle(b)
    assert [g.id for g in a] == [g.id for g in b]


def test_a_different_seed_gives_a_different_order():
    a, b = _ordered_like_the_benchmark(), _ordered_like_the_benchmark()
    random.Random(DEFAULT_SEED).shuffle(a)
    random.Random(DEFAULT_SEED + 1).shuffle(b)
    assert [g.id for g in a] != [g.id for g in b]


def test_the_flags_exist_and_shuffle_defaults_off():
    """Off by default, so every historical command reproduces exactly what it
    did before."""
    from scripts.evaluate_proofs import main
    import inspect

    source = inspect.getsource(main)
    assert '"--shuffle"' in source
    assert '"--seed"' in source
    assert 'action="store_true"' in source, (
        "--shuffle must default to off or old commands change meaning"
    )


def test_the_run_record_says_whether_it_shuffled():
    """Without it a partial run's rate cannot be read at all: front-loaded and
    cross-tier samples give very different numbers from the same goals file."""
    from scripts.evaluate_proofs import invocation

    class Args:
        goals, budget_profile, limit, tier, goal = "f.json", None, None, None, None
        shuffle, seed = True, 4242

    record = invocation(Args(), {})
    assert record["shuffle"] is True
    assert record["seed"] == 4242

    class Off(Args):
        shuffle, seed = False, 4242

    assert invocation(Off(), {})["seed"] is None, (
        "a seed recorded for an unshuffled run implies an order it did not use"
    )
