"""A mixed benchmark, so the per-tier report finally has rows.

WHY THIS EXISTS
---------------
`eval.proof_metrics.summarize` has always emitted a per-tier proof rate, and
it has never once been populated. Every run in this project's history drew
from a single source, so the report reads

    in-mathlib   n/a (no such cases)
    near-mathlib n/a (no such cases)
    ...
    putnam       40%

One number says how good the agent is; a gradient says WHERE it works, which
is more useful and much harder to argue with.

MEASURED, aggregated over every file in eval/results/, and the real reason
this matters:

    tier          calls/goal   proved     source
    in-mathlib             8   6/6  100%  written by this project
    near-mathlib           6  52/57  91%  written by this project
    deep                  13   4/5   80%  written by this project
    hard                  17  14/26  54%  written by this project
    putnam                26   4/17  24%  EXTERNAL
    proofnet              13   7/75   9%  EXTERNAL

The tiers this agent excels at are the ones it was developed against.
Publishing the gradient WITH its sources is the honest version and the
stronger one.
"""

import collections
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mixed_benchmark  # noqa: E402
from eval.proof_dataset import Tier, load_goals  # noqa: E402


def test_the_curated_set_is_taken_whole():
    """Not sampled: at 25 goals over five tiers it is already small, and
    dropping any empties a row -- `novel` has two goals, `in-mathlib` six."""
    picked = mixed_benchmark.curated()

    assert len(picked) == len(load_goals())
    tiers = collections.Counter(g["tier"] for g in picked)
    assert tiers["novel"] == 2 and tiers["in-mathlib"] == 6


def test_a_mix_populates_every_tier_the_report_prints(tmp_path):
    """THE point of the exercise. A tier with no goals prints
    "n/a (no such cases)", which is how every run so far has read."""
    out = tmp_path / "mixed.json"
    proofnet = tmp_path / "pn.json"
    proofnet.write_text(json.dumps([
        {"id": f"exercise_{n}", "area": "proofnet 1", "goal": "g",
         "tier": "proofnet"} for n in range(5)]), encoding="utf-8")

    mixed_benchmark.main(["--curated", f"--add", f"{proofnet}:5",
                          "--out", str(out)])

    goals = load_goals(out)
    present = {g.tier for g in goals}
    for tier in (Tier.IN_MATHLIB, Tier.NEAR_MATHLIB, Tier.NOVEL,
                 Tier.HARD, Tier.DEEP, Tier.PROOFNET):
        assert tier in present, tier


def test_an_overlapping_id_cannot_inflate_a_tier(tmp_path):
    """Two sources sharing a goal would count it twice and quietly change a
    tier's denominator -- the number a rate is divided by."""
    out = tmp_path / "mixed.json"
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    goal = [{"id": "putnam_1962_a1", "area": "putnam 1962", "goal": "g",
             "tier": "putnam"}]
    a.write_text(json.dumps(goal), encoding="utf-8")
    b.write_text(json.dumps(goal), encoding="utf-8")

    mixed_benchmark.main([f"--add", f"{a}:1", "--add", f"{b}:1",
                          "--out", str(out)])

    assert len(json.loads(out.read_text(encoding="utf-8"))) == 1


def test_taking_n_reads_the_head_of_an_already_sampled_file(tmp_path):
    """The FIRST n, not a fresh shuffle. Re-shuffling here would make the mix
    depend on two seeds, and only the sampler's gets recorded."""
    src = tmp_path / "s.json"
    src.write_text(json.dumps([
        {"id": f"g{n}", "area": "a", "goal": "g", "tier": "putnam"}
        for n in range(10)]), encoding="utf-8")

    picked = mixed_benchmark.take(str(src), 3)

    assert [g["id"] for g in picked] == ["g0", "g1", "g2"]


def test_selecting_nothing_is_an_error_not_an_empty_benchmark(tmp_path):
    """An empty goals file would run in seconds and report a proof rate of
    n/a, which reads like a finished run."""
    assert mixed_benchmark.main(["--out", str(tmp_path / "x.json")]) == 2


def test_the_committed_mix_covers_all_seven_tiers():
    """The artefact itself, so a later edit cannot silently empty a row."""
    path = Path(__file__).resolve().parent.parent / "eval" / "mixed-benchmark.json"
    if not path.exists():
        pytest.skip("eval/mixed-benchmark.json has not been generated")

    tiers = collections.Counter(g.tier for g in load_goals(path))

    assert len(tiers) == 7, dict(tiers)
    assert tiers[Tier.PROOFNET] and tiers[Tier.PUTNAM], (
        "the external sources are what keep this honest"
    )
