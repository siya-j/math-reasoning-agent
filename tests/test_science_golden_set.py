"""The science golden set is a separate file on purpose.

`eval/golden.json` is the maths regression gate and its headline numbers are
tracked run to run.  Mixing science cases into it would change that baseline
into one number that is really two.  These tests guard the separation and the
shape of the new set.
"""

import json
from pathlib import Path

from eval.dataset import load_cases

SCIENCE = Path(__file__).resolve().parents[1] / "eval" / "golden-science.json"


def test_the_science_set_loads_through_the_ordinary_loader():
    cases = load_cases(SCIENCE)
    assert len(cases) == len(json.loads(SCIENCE.read_text()))
    assert cases, "the set is empty"


def test_every_id_is_unique():
    ids = [row["id"] for row in json.loads(SCIENCE.read_text())]
    assert len(set(ids)) == len(ids)


def test_it_does_not_overlap_the_maths_gate():
    science = {c.id for c in load_cases(SCIENCE)}
    maths = {c.id for c in load_cases()}
    assert not (science & maths)


def test_it_covers_every_domain_the_agent_claims_to_serve():
    areas = {c.area for c in load_cases(SCIENCE)}
    assert {"physics", "chemistry", "biology", "units"} <= areas


def test_it_carries_deliberately_wrong_claims():
    """Without these the set measures capability but never soundness."""
    wrong = [c for c in load_cases(SCIENCE) if c.expected == "false"]
    assert len(wrong) >= 10


def test_it_carries_cases_nothing_can_decide():
    """These measure restraint: the agent must not answer them at all."""
    undecidable = [c for c in load_cases(SCIENCE) if c.expected == "n/a"]
    assert len(undecidable) >= 5
