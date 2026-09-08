"""A refutation must survive into the results file and be re-auditable.

MEASURED, and it is the asymmetry that makes it a bug rather than an omission.
A PROVED goal's Lean source is retained deliberately -- `ProofResult` says so:
"a system whose entire assertion is 'the compiler accepted this' must retain
what the compiler accepted." A REFUTED goal retained NOTHING. `proof` was
empty, `stages` carried no refutation entry, and `verify_results.py` did not
know refutations existed.

So the two strongest results this agent has produced were recheckable only
from `tempfile.mkdtemp` workspaces that Windows clears on its own schedule:
`exercise_5_15` and `exercise_3_22`, both ACCEPTED compilations of the
negation of a ProofNet statement. ProofNet's exercise_3_22 states Baire's
theorem without `[Nonempty X]`, and on the empty space the empty set is both
open and dense, so the hypotheses hold vacuously while the conclusion cannot.
That finding rested on a temp directory.

A refutation is a compiler fact of exactly the same standing as a proof, and
often the more consequential one, because it says a published benchmark is
wrong.
"""

import asyncio
import json

import pytest

from domain.proof import ProofRun
from math_v2 import harness
from math_v2.core import log
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = "theorem broken (n : ℕ) : n > 0"
NEGATION = "theorem broken_refutation : ¬ (∀ n : ℕ, n > 0)"
NEG_PROOF = "by\n  intro h\n  exact absurd (h 0) (by simp)"


def run(coro):
    return asyncio.run(coro)


async def accepts(source):
    return LeanResult(LeanOutcome.COMPILED, "")


@pytest.fixture
def refuted(tmp_path):
    """A workdir carrying a genuinely accepted refutation, produced by the
    real `try_refutation` rather than a hand-written record."""
    from math_v2.core import proving

    workdir = str(tmp_path)
    log.clear(workdir)
    run(proving.check_statement(workdir, GOAL, accepts))
    result = run(proving.try_refutation(workdir, NEGATION, NEG_PROOF, accepts))
    assert result["outputs"].get("refuted"), result
    return workdir


# --------------------------------------------------- retained on the run
def test_the_harness_keeps_the_refutation(refuted):
    """`verdict.verified_refutation` already returned this record; nothing
    read it."""
    result = harness._to_proof_run(ProofRun(goal="q"), refuted, prose="",
                                   seconds=0.0)

    assert result.refutation.strip() == NEG_PROOF.strip(), (
        "the refutation's proof body was dropped"
    )
    assert result.refutation_statement.strip() == NEGATION.strip(), (
        "the NEGATION is what was compiled; without it nothing can recompile"
    )


def test_it_reaches_the_results_file(refuted):
    """End to end, through the same conversion an evaluation run uses.

    The OUTCOME is deliberately not asserted here. `classify` reads REFUTED
    from a trace note that only `finish` writes, and this fixture drives
    `try_refutation` without reporting -- so the outcome is `not_proved`,
    correctly. The artefact is what this test is about, and the next test is
    about that separation being a feature.
    """
    from eval.proof_dataset import Tier
    from eval.proof_metrics import result_from

    class Goal:
        id, area, tier = "broken", "number theory", Tier.PROOFNET

    row = result_from(Goal(), harness._to_proof_run(
        ProofRun(goal="q"), refuted, prose="", seconds=0.0))

    assert row.refutation.strip() == NEG_PROOF.strip()
    assert row.refutation_statement.strip() == NEGATION.strip()


def test_the_artefact_survives_even_when_the_report_does_not(refuted):
    """The evidence is transcribed from the RECORD, not from the outcome, so a
    run that compiled a refutation and then crashed before calling `finish`
    still carries the proof of it.

    That separation is the point. `exercise_3_22` crashed outright on the
    previous run (`agent failed: ReadError` at 26/40 compiles); had it crashed
    a moment later, after the refutation compiled, the old code would have
    kept nothing at all and the finding would have been lost with the
    workspace.
    """
    from eval.proof_metrics import ProofOutcome, classify

    result = harness._to_proof_run(ProofRun(goal="q"), refuted, prose="",
                                   seconds=0.0)

    assert classify(result) is not ProofOutcome.REFUTED, (
        "this fixture never reported, so the outcome must not claim refuted"
    )
    assert result.refutation.strip(), (
        "the artefact was discarded because the report never happened"
    )


def test_a_proved_goal_carries_no_refutation(tmp_path):
    """The default stays empty, so a proved row is unchanged and every
    historical results file still reads correctly."""
    workdir = str(tmp_path)
    log.clear(workdir)
    result = harness._to_proof_run(ProofRun(goal="q"), workdir, prose="",
                                   seconds=0.0)
    assert result.refutation == ""
    assert result.refutation_statement == ""


def test_a_resume_carries_the_refutation(tmp_path):
    """Free, because `rehydrate` reads the field list off the dataclass -- the
    payoff of driving it that way instead of naming fields by hand."""
    from eval.proof_dataset import Tier
    from eval.proof_metrics import ProofOutcome, ProofResult, rehydrate
    from scripts.evaluate_proofs import completed, save

    original = ProofResult(
        goal_id="broken", area="number theory", tier=Tier.PROOFNET,
        outcome=ProofOutcome.REFUTED, statement=GOAL,
        refutation=NEG_PROOF, refutation_statement=NEGATION)
    out = tmp_path / "run.json"
    save([original], {}, out)

    carried = completed(True, out)[0]
    assert carried.refutation == NEG_PROOF
    assert carried.refutation_statement == NEGATION


# ------------------------------------------------ auditable afterwards
def test_verify_results_audits_a_refutation(tmp_path):
    """It checked only `proved` rows before, so a refutation was never
    rechecked -- the claim with the most external consequence was the one
    claim nobody audited."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "vr", "scripts/verify_results.py")
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)

    out = tmp_path / "run.json"
    out.write_text(json.dumps({"results": [{
        "goal_id": "broken", "outcome": "refuted", "statement": GOAL,
        "refutation": NEG_PROOF, "refutation_statement": NEGATION,
        "lemmas": [],
    }]}))

    claims = vr.claims_in(out)
    assert len(claims) == 1, "the refutation was not picked up for audit"
    assert claims[0]["kind"] == "refutation"

    # The NEGATION is what gets recompiled -- recompiling the goal would
    # silently check the wrong statement and could only ever fail.
    # `build_source` renames the LAST declaration to `mra_goal`, so the
    # refutation's own name does not survive -- what must survive is the
    # NEGATED claim itself.
    source = vr.source_for(claims[0])
    assert "¬" in source, source
    assert "(∀ n : ℕ, n > 0)" in source, source


def test_a_refuted_row_with_no_source_is_not_audited(tmp_path):
    """Every results file written before this change has `outcome: refuted`
    and no refutation text. Those must be skipped, not reported as failures."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "vr", "scripts/verify_results.py")
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)

    out = tmp_path / "old.json"
    out.write_text(json.dumps({"results": [
        {"goal_id": "old", "outcome": "refuted", "statement": GOAL},
    ]}))

    assert vr.claims_in(out) == []
