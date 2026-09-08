"""A resume must carry every field, and this test must not be able to miss one.

MEASURED, the incident. `--resume` rebuilt each carried-forward goal by hand
from ten named fields while `ProofResult` had grown to twenty-four. `save`
writes the whole `__dict__` back, so resuming `eval/results/mixed-1.json`
wrote the other fourteen straight over the real run at their dataclass
defaults: every accepted proof's Lean source, every attempt, every trace and
all telemetry for thirty-two already-decided goals. Four of those fields --
`model_calls`, `input_tokens`, `output_tokens`, `seconds` -- are read off the
agent's transcript and exist nowhere else, so roughly 10.1M input tokens of
cost accounting is simply gone. The outcomes survived, so the run printed an
ordinary-looking summary while doing it.

THE TEST IS BUILT OFF `dataclasses.fields`, deliberately, because a test that
listed the fields itself would have exactly the defect it is guarding against:
it would pass on the day someone adds field twenty-five, which is the day the
next resume starts dropping it.
"""

import dataclasses
import json

import pytest

from eval.proof_dataset import Tier
from eval.proof_metrics import ProofOutcome, ProofResult, rehydrate


def _distinctive(field):
    """A value for `field` that is NOT its default.

    Every field must differ from its default, or the round-trip assertion
    passes vacuously: comparing defaults to defaults proves nothing about
    whether anything was carried.
    """
    if field.name == "tier":
        return Tier.PROOFNET
    if field.name == "outcome":
        return ProofOutcome.PROVED
    default = field.default
    if isinstance(default, bool):
        return not default
    if isinstance(default, tuple):
        return (f"{field.name}-one", f"{field.name}-two")
    if isinstance(default, float):
        return 12.5
    if isinstance(default, int):
        return 7
    return f"{field.name}-value"


def _fully_populated():
    return ProofResult(**{f.name: _distinctive(f)
                          for f in dataclasses.fields(ProofResult)})


def test_every_field_has_a_non_default_value_in_the_fixture():
    """Guards the guard. If `_distinctive` ever returns a default -- say a new
    field whose default is 7 -- the round-trip below stops testing that field
    without any test turning red."""
    result = _fully_populated()
    same = [f.name for f in dataclasses.fields(ProofResult)
            if getattr(result, f.name) == f.default]
    assert not same, f"fixture leaves these at their defaults: {same}"


def test_a_resume_carries_every_field(tmp_path):
    """THE regression: save a result, resume, and get the same result back."""
    from scripts.evaluate_proofs import completed, save

    original = _fully_populated()
    out = tmp_path / "run.json"
    save([original], {}, out)

    carried = completed(True, out)

    assert len(carried) == 1
    lost = [f.name for f in dataclasses.fields(ProofResult)
            if getattr(carried[0], f.name) != getattr(original, f.name)]
    assert not lost, f"a resume dropped these fields: {lost}"


def test_the_evidence_for_a_proof_survives_a_resume(tmp_path):
    """Named separately because it is the field whose loss actually cost
    something: `scripts/verify_results.py` recompiles a results file with no
    model, no agent and no surviving workspace, and it needs `proof` and the
    `lemmas` the proof cites by name. A resume that drops them turns every
    recorded proof into an unverifiable claim."""
    from scripts.evaluate_proofs import completed, save

    proved = ProofResult(
        goal_id="g", area="number theory", tier=Tier.PROOFNET,
        outcome=ProofOutcome.PROVED,
        statement="theorem t : 2 + 2 = 4",
        proof="by\n  have h := helper\n  norm_num",
        lemmas=("lemma helper : 1 = 1 := by rfl",),
    )
    out = tmp_path / "run.json"
    save([proved], {}, out)

    carried = completed(True, out)[0]

    assert carried.proof == proved.proof, "the accepted Lean source was lost"
    assert carried.lemmas == proved.lemmas, (
        "the lemmas the proof cites by name were lost, so it cannot recompile"
    )


def test_an_unknown_field_in_the_file_does_not_break_a_resume(tmp_path):
    """A results file from a newer build must still resume rather than raise.
    The reverse of the bug, and the reason `rehydrate` ignores extra keys."""
    from scripts.evaluate_proofs import completed

    row = {f.name: _distinctive(f) for f in dataclasses.fields(ProofResult)}
    row["tier"] = Tier.PROOFNET.value
    row["outcome"] = ProofOutcome.PROVED.value
    row["a_field_from_the_future"] = "hello"

    out = tmp_path / "run.json"
    out.write_text(json.dumps({"results": [row]}))

    carried = completed(True, out)
    assert len(carried) == 1
    assert carried[0].goal_id == "goal_id-value"


def test_errors_are_still_not_carried(tmp_path):
    """Unchanged behaviour, asserted because `rehydrate` sits right next to
    the check: a goal that never reached the model was not decided, and
    carrying it would bake a quota outage into the results."""
    from scripts.evaluate_proofs import completed, save

    crashed = dataclasses.replace(_fully_populated(),
                                  outcome=ProofOutcome.ERROR)
    out = tmp_path / "run.json"
    save([crashed], {}, out)

    assert completed(True, out) == []
