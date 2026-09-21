"""A proof that never uses the conclusion proves nothing, and it RECOMPILES.

MEASURED on heldout-even-63: two of forty-two accepted proofs are vacuous, and
both pass `verify_results.py`'s recompilation check, because that check asks
only "did the compiler accept it" and the answer is honestly yes.

    Ireland-Rosen_exercise_2_21   `∀ p n, p.Prime → l (p^n) = log p` at n = 0
                                  gives `l 1 = log 2` AND `l 1 = log 3`;
                                  `linarith` on that contradiction closes any
                                  goal at all
    Herstein_exercise_2_8_12      `IsEmpty (CommGroup G)` is unsatisfiable for
                                  a carrier of card 21, so the proof derives
                                  False and `.elim`s it

`says_nothing` cannot catch either: it reads the CONCLUSION, and both
conclusions are meaningful. The defect is in the hypotheses.

VERIFIED AGAINST REAL LEAN before this was written: the probe caught both of
the above and left `Munkres_exercise_16_4` and `Axler_exercise_1_3` alone.

THE PROBE LIVES IN THE AUDIT, not in `try_proof`. Wiring it into the agent
loop broke ten tests whose fake compiler accepts any source -- including
`theorem foo : False` -- and, worse, would have mixed a behavioural change
into a run being measured for three unrelated bug fixes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import verify_results  # noqa: E402
from verifiers.lean_runner import LeanOutcome, LeanResult  # noqa: E402

VACUOUS_CLAIM = {
    "goal_id": "exercise_2_21",
    "kind": "proof",
    "statement": (
        "theorem exercise_2_21 {l : ℕ → ℝ} "
        "(hl : ∀ p n : ℕ, p.Prime → l (p^n) = log p) : l = fun n => n"
    ),
    "proof": "by linarith",
    "lemmas": [],
}


def lean(probe_outcome, real_outcome=LeanOutcome.COMPILED):
    """A compiler that answers the `False` probe differently from the proof."""
    seen = []

    def go(source, **_):
        seen.append(source)
        vacuity_probe = ": False" in source
        return LeanResult(probe_outcome if vacuity_probe else real_outcome, "")

    go.seen = seen
    return go


# ------------------------------------------------------------ the probe source
def test_the_probe_replaces_the_conclusion_and_keeps_everything_else():
    """The whole check rests on this: same binders, same proof, `False` goal."""
    source = verify_results.vacuity_source_for(VACUOUS_CLAIM)

    assert ": False" in source, "the conclusion was not swapped"
    assert "hl" in source, "the hypotheses must survive -- they are the suspects"
    assert "l = fun n => n" not in source, "the original conclusion leaked in"
    assert "by linarith" in source, "the probe must offer the SAME proof"


def test_a_statement_with_no_hypotheses_is_never_probed():
    """`theorem g : 2 + 2 = 4` binds nothing, so nothing can clash.

    Not an optimisation. A compile spent here could only come back negative.
    """
    claim = dict(VACUOUS_CLAIM, statement="theorem g : 2 + 2 = 4")
    assert verify_results.vacuity_source_for(claim) is None


def test_an_unparseable_statement_is_never_probed():
    claim = dict(VACUOUS_CLAIM, statement="not a theorem at all")
    assert verify_results.vacuity_source_for(claim) is None


# ----------------------------------------------------------------- the verdict
def test_a_proof_that_also_closes_False_is_reported_vacuous():
    ok, note = verify_results.check(VACUOUS_CLAIM, lean(LeanOutcome.COMPILED))

    assert ok is verify_results.VACUOUS
    assert "VACUOUS" in note


def test_a_proof_that_cannot_close_False_simply_recompiled():
    ok, note = verify_results.check(VACUOUS_CLAIM, lean(LeanOutcome.ERRORS))

    assert ok is True
    assert note == "recompiled"


def test_the_probe_is_only_run_after_the_proof_itself_recompiles():
    """A proof that does not recompile is a failure; vacuity never arises."""
    runner = lean(LeanOutcome.COMPILED, real_outcome=LeanOutcome.ERRORS)
    ok, _ = verify_results.check(VACUOUS_CLAIM, runner)

    assert ok is False
    assert len(runner.seen) == 1, "it probed a proof that had already failed"


def test_vacuous_is_not_mistaken_for_a_pass_by_a_falsiness_check():
    """`VACUOUS` is a non-empty string, so `not ok` is False.

    The aggregation loop tests it explicitly for exactly this reason -- a bare
    `elif not ok` drops it into no bucket at all and reports it as verified.
    """
    assert verify_results.VACUOUS
    assert verify_results.VACUOUS is not True
