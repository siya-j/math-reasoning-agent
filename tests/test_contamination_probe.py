"""The recall probe's comparison must be measuring the right thing.

AN EARLIER VERSION OF THIS SCRIPT MEASURED NOTHING. It compared the agent's
accepted PROOF against ProofNet's reference text by 5-gram overlap -- but
every ProofNet entry ships its statement with the proof replaced by `sorry`
(0 of 182 notes contain `:= by`), so there was no reference proof to compare
against and the number was just "how much of the signature does the proof
happen to restate". These tests exist because that mistake was shipped and
caught, and the fix rests on two small text operations that are easy to get
wrong silently.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _probe():
    spec = importlib.util.spec_from_file_location(
        "contamination", ROOT / "scripts" / "contamination.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOTE = """open Topology Filter Real Complex
open scoped BigOperators

theorem exercise_1_18a
  (n : ℕ)
  (h : n > 1)
  (x : EuclideanSpace ℝ (Fin n))
  : ∃ (y : EuclideanSpace ℝ (Fin n)), y ≠ 0 ∧ inner x y = 0:= sorry"""


def test_the_reference_drops_the_sorry():
    """`:= sorry` is in every entry. Leaving it in gives every answer free
    similarity, since the probe asks for a signature and no model would
    volunteer `sorry`."""
    ref = _probe().reference(NOTE)
    assert "sorry" not in ref, ref


def test_the_reference_drops_the_open_preamble():
    """The `open ...` lines are boilerplate shared by every goal in a chapter.
    Left in, they would give each answer a floor of similarity for free and
    compress the very differences the probe is looking for."""
    ref = _probe().reference(NOTE)
    assert "open Topology" not in ref, ref
    assert "open scoped" not in ref, ref
    # ...and the theorem itself survives.
    assert "theorem exercise_1_18a" in ref
    assert "EuclideanSpace" in ref


def test_similarity_is_order_insensitive_but_not_blind():
    probe = _probe()
    ref = probe.reference(NOTE)
    assert probe.jaccard(ref, ref) == 1.0
    assert probe.jaccard(ref, "theorem unrelated : 2 + 2 = 4") < 0.2
    # Whitespace is not a signal: reflowing must not change the score much.
    reflowed = " ".join(ref.split())
    assert probe.jaccard(ref, reflowed) > 0.95


def test_a_declined_answer_scores_zero_not_a_coincidence():
    """The prompt offers `UNKNOWN` deliberately, so a model that has not seen
    a theorem can say so rather than inventing a plausible-looking statement
    that would score as partial recall."""
    probe = _probe()
    assert "UNKNOWN" in probe.ASK
    assert probe.jaccard("UNKNOWN", probe.reference(NOTE)) < 0.1


def test_no_reference_proofs_exist_which_is_why_the_design_changed():
    """The finding that invalidated the first version, asserted so nobody
    rebuilds it: ProofNet ships statements, not proofs."""
    path = ROOT / "eval" / "proofnet-182.json"
    if not path.exists():
        pytest.skip("proofnet-182.json is not present")
    goals = json.loads(path.read_text(encoding="utf-8"))
    with_proof = [g["id"] for g in goals if ":= by" in (g.get("note") or "")]
    assert not with_proof, (
        f"reference PROOFS now exist for {len(with_proof)} goals, so a "
        f"proof-similarity check is possible after all and this script's "
        f"design should be revisited"
    )


def test_the_dry_run_is_the_default_and_spends_nothing(capsys):
    """`--run` is opt-in because this script is the only analysis tool here
    that costs money."""
    probe = _probe()
    code = probe.main(["--goals", "eval/proofnet-182.json", "--limit", "2"])
    assert code == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Nothing is spent" in out
