"""Offline tests for the proving pipeline (Prover Agent, §3).

No model, no API key, no Lean. The formalizer and the compiler are both
injected, which is the point: everything tested here is STRATEGY — how many
attempts, when to refine, when to decompose, what happens on failure — and
strategy should not need a GPU or a 3 GB toolchain to verify.
"""

import config
from domain.proof import ProofStage
from domain.verdict import Verdict, VerificationStatus as S
from pipeline.prover import prove

ACCEPTED = Verdict(S.TRUE, "lean", "Lean accepted a complete proof.")
REJECTED = Verdict(S.UNKNOWN, "lean", "error: unknown identifier 'nonsense'")


class FakeFormalizer:
    """Replays scripted answers and records what it was asked.

    The statement is derived from the goal so that a fake compiler can tell
    the main theorem apart from a lemma, which is what the synthesis tests
    need to express.
    """

    def __init__(self, statement=None, lemmas=None, skeleton="by attempt"):
        self._statement = statement
        self._skeleton = skeleton
        self._lemmas = list(lemmas or [])
        self.proof_calls = []
        self.drafts_sent = []
        self.synthesis_calls = []

    def statement(self, goal):
        if self._statement is not None:
            return self._statement
        return f"theorem {goal.replace(' ', '_')}"

    def sketch(self, goal):
        return "informal sketch"

    def proof(self, statement, sketch, errors="", previous=""):
        self.proof_calls.append(errors)
        self.drafts_sent.append(previous)
        return "by attempt"

    def skeleton(self, statement, sketch, count=4):
        return self._skeleton

    def hole(self, claim, context, statement=""):
        self.holes_filled = getattr(self, "holes_filled", 0) + 1
        # distinctive: the mechanical ladder also contains `trivial`
        return "model_fill"

    def lemmas(self, goal, count):
        return self._lemmas[:count]

    def synthesis(self, statement, lemmas):
        self.synthesis_calls.append(lemmas)
        return "by synthesised"


def compiler(predicate):
    """A fake Lean: accepts exactly what `predicate(statement, proof)` allows."""

    def check(statement, proof):
        return ACCEPTED if predicate(statement, proof) else REJECTED

    return check


ACCEPTS_EVERYTHING = compiler(lambda statement, proof: True)
ACCEPTS_NOTHING = compiler(lambda statement, proof: False)


def accepts_after(failures):
    """Rejects the first `failures` proposals, then accepts."""
    seen = {"n": 0}

    def check(statement, proof):
        seen["n"] += 1
        return REJECTED if seen["n"] <= failures else ACCEPTED

    return check


# ------------------------------------------------------------ direct proving
def test_standard_tactics_close_a_goal_without_any_model_call():
    """The point of the mechanical attempt: when `exact <lemma>` works, no
    sketch-and-generate round trip is paid for."""
    fake = FakeFormalizer()
    run = prove("True holds", formalizer=fake, check=ACCEPTS_EVERYTHING)

    assert run.proved
    assert [a.stage for a in run.attempts] == [ProofStage.CHEAP]
    assert fake.proof_calls == [], "a model call was spent unnecessarily"
    assert run.lemmas == []


def test_the_model_is_used_when_standard_tactics_fail():
    fake = FakeFormalizer()
    run = prove(
        "harder", formalizer=fake, check=accepts_after(1), depth=0
    )  # the cheap attempt fails, the first direct one succeeds

    assert run.proved
    assert [a.stage for a in run.attempts] == [ProofStage.CHEAP, ProofStage.DIRECT]
    assert fake.proof_calls, "the model was never asked"


def test_direct_attempts_are_tried_before_refinement():
    fake = FakeFormalizer()
    run = prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=0)

    stages = [a.stage for a in run.attempts]
    assert stages[0] is ProofStage.CHEAP
    assert stages[1 : 1 + config.PROOF_ATTEMPTS] == [ProofStage.DIRECT] * config.PROOF_ATTEMPTS
    assert ProofStage.REFINE in stages


def test_a_claim_that_cannot_be_formalised_is_refused():
    run = prove("???", formalizer=FakeFormalizer(statement="  "), check=ACCEPTS_EVERYTHING)

    assert not run.proved
    assert run.verdict.status is S.UNKNOWN
    assert run.attempts == []


# --------------------------------------------------------------- refinement
def test_refinement_receives_the_compiler_errors():
    """The whole value of refinement is feeding the error back in."""
    fake = FakeFormalizer()
    prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=0)

    refinement_calls = fake.proof_calls[config.PROOF_ATTEMPTS:]
    assert refinement_calls, "no refinement happened"
    assert all("unknown identifier" in errors for errors in refinement_calls)


def test_the_first_direct_attempt_gets_no_error_context():
    fake = FakeFormalizer()
    prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=0)
    assert fake.proof_calls[0] == ""


def test_refinement_can_recover_a_proof():
    fake = FakeFormalizer()
    run = prove(
        "recoverable",
        formalizer=fake,
        check=accepts_after(config.PROOF_ATTEMPTS + 1),   # +1 for the cheap attempt
        depth=0,
    )

    assert run.proved
    assert run.attempts[-1].stage is ProofStage.REFINE


def test_refinement_repairs_the_draft_with_the_fewest_errors():
    """Prover Agent §3.1: refine the closest attempt, not the most recent."""
    from domain.proof import ProofAttempt, ProofStage
    from pipeline.prover import best_draft
    from domain.proof import ProofRun

    run = ProofRun(goal="g")
    run.attempts = [
        ProofAttempt(1, ProofStage.DIRECT, "three errors",
                     Verdict(S.UNKNOWN, "lean", "error: a\nerror: b\nerror: c")),
        ProofAttempt(2, ProofStage.DIRECT, "one error",
                     Verdict(S.UNKNOWN, "lean", "error: a")),
    ]
    assert best_draft(run).proof == "one error"


def test_the_previous_attempt_is_sent_back_for_repair():
    """The model must see what it wrote, not only what was wrong with it."""
    fake = FakeFormalizer()
    prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=0)

    refinement_drafts = fake.drafts_sent[config.PROOF_ATTEMPTS:]
    assert refinement_drafts, "no refinement happened"
    assert all(draft == "by attempt" for draft in refinement_drafts)


def test_direct_attempts_are_sent_no_draft():
    fake = FakeFormalizer()
    prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=0)
    assert fake.drafts_sent[0] == ""


def test_attempts_are_bounded():
    fake = FakeFormalizer()
    run = prove("impossible", formalizer=fake, check=ACCEPTS_NOTHING, depth=0)

    # +1: the deterministic `first | ...` attempt precedes the model ones
    assert len(run.attempts) == 1 + config.PROOF_ATTEMPTS + config.PROOF_REFINEMENTS


# ------------------------------------------------------------------ skeleton
SKELETON = """by
  have h1 : 0 < n := by sorry
  have h2 : n ≠ 0 := by sorry
  exact foo h1 h2"""


def test_a_skeleton_that_does_not_typecheck_is_abandoned():
    """Filling holes in a broken decomposition is wasted effort — the failure
    is structural, not something a subgoal tactic can repair."""
    fake = FakeFormalizer(skeleton=SKELETON)
    run = prove(
        "hard",
        formalizer=fake,
        check=ACCEPTS_NOTHING,
        depth=0,
        structure_check=lambda statement, proof: False,
    )

    assert getattr(fake, "holes_filled", 0) == 0, "holes filled in a broken skeleton"
    assert any("does not typecheck" in entry for entry in run.trace)


def test_holes_are_closed_mechanically_before_any_model_call():
    """A subgoal is exactly the size `simp` or a cited premise tends to close."""
    fake = FakeFormalizer(skeleton=SKELETON)
    prove(
        "hard",
        formalizer=fake,
        check=ACCEPTS_NOTHING,
        depth=0,
        structure_check=lambda statement, proof: True,   # every fill accepted
    )
    assert getattr(fake, "holes_filled", 0) == 0, "a model call was spent on a hole"


def test_the_model_fills_holes_the_tactics_cannot():
    fake = FakeFormalizer(skeleton=SKELETON)
    # The skeleton typechecks; mechanical fills do not. A mechanical fill is
    # recognisable by its `first | ...` block, which a model fill never has.
    def structure(statement, proof):
        return "first" not in proof

    prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=0,
          structure_check=structure)

    assert getattr(fake, "holes_filled", 0) == 2, "the model never closed a hole"


def test_a_filled_skeleton_that_compiles_is_a_proof():
    fake = FakeFormalizer(skeleton=SKELETON)
    attempts = 1 + config.PROOF_ATTEMPTS + config.PROOF_REFINEMENTS
    run = prove(
        "hard",
        formalizer=fake,
        check=accepts_after(attempts),   # only the skeleton attempt is accepted
        depth=0,
        structure_check=lambda statement, proof: True,
    )

    assert run.proved
    assert run.attempts[-1].stage is ProofStage.SKELETON


def test_a_proof_without_holes_is_not_treated_as_a_skeleton():
    """The direct path already covered it; re-checking wastes a compile."""
    fake = FakeFormalizer(skeleton="by norm_num")
    run = prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=0)

    assert ProofStage.SKELETON not in [a.stage for a in run.attempts]


# ---------------------------------------------------------- auxiliary lemmas
def test_lemmas_are_generated_only_after_direct_proving_fails():
    fake = FakeFormalizer(lemmas=["a special case"])
    run = prove("easy", formalizer=fake, check=ACCEPTS_EVERYTHING)
    assert run.lemmas == []


def test_lemmas_are_attempted_when_direct_proving_fails():
    fake = FakeFormalizer(lemmas=["case a", "case b"])
    run = prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=1)

    assert [lemma.informal for lemma in run.lemmas] == ["case a", "case b"]
    assert run.proved_lemmas == []


def test_lemma_recursion_is_bounded_by_depth():
    """A lemma that also fails must not spawn lemmas of its own at depth 1."""
    fake = FakeFormalizer(lemmas=["case a"])
    run = prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=1)

    assert len(run.lemmas) == 1
    assert all(not lemma.is_proved for lemma in run.lemmas)


def test_depth_zero_skips_lemmas_entirely():
    fake = FakeFormalizer(lemmas=["case a"])
    run = prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=0)
    assert run.lemmas == []
    assert fake.synthesis_calls == []


# ------------------------------------------------------------------ synthesis
def test_proved_lemmas_are_used_to_synthesise_a_final_proof():
    """Safe only because the synthesised proof is itself compiled."""
    fake = FakeFormalizer(lemmas=["case a"])
    # the lemma compiles, and so does the proof synthesised from it
    run = prove("hard", formalizer=fake, check=compiler(lambda statement, proof: "case_a" in statement or "synthes" in proof), depth=1)

    assert run.proved_lemmas, "the lemma should have been proved"
    assert fake.synthesis_calls, "synthesis never ran"
    assert run.attempts[-1].stage is ProofStage.SYNTHESIS
    assert run.proved


def test_synthesis_is_skipped_when_no_lemma_was_proved():
    fake = FakeFormalizer(lemmas=["case a"])
    run = prove("hard", formalizer=fake, check=ACCEPTS_NOTHING, depth=1)
    assert fake.synthesis_calls == []
    assert not run.proved


def test_a_rejected_synthesis_does_not_count_as_proved():
    """The compiler is the only thing that can turn lemmas into a proof."""
    fake = FakeFormalizer(lemmas=["case a"])
    run = prove("hard", formalizer=fake, check=compiler(lambda statement, proof: "case_a" in statement and "synthes" not in proof), depth=1)

    assert run.proved_lemmas
    assert not run.proved
    assert run.verdict.status is S.UNKNOWN


# ------------------------------------------------------------ honest failure
def test_failure_is_never_reported_as_refutation():
    """Not finding a proof says nothing about the claim being false."""
    run = prove("hard", formalizer=FakeFormalizer(), check=ACCEPTS_NOTHING, depth=0)

    assert run.verdict.status is S.UNKNOWN
    assert run.verdict.status is not S.FALSE
    assert "not evidence that the claim is false" in run.verdict.detail


def test_failure_still_reports_lemmas_that_were_proved():
    fake = FakeFormalizer(lemmas=["case a"])
    run = prove("hard", formalizer=fake, check=compiler(lambda statement, proof: "case_a" in statement and "synthes" not in proof), depth=1)

    assert "auxiliary lemma(s) were proved" in run.verdict.detail


# ---------------------------------------------------------------- reporting
def test_the_report_shows_the_formal_statement_for_human_review():
    """The compiler guarantees the proof; only a human can check the statement."""
    fake = FakeFormalizer(statement="theorem foo : 1 + 1 = 2")
    run = prove("is 1+1=2", formalizer=fake, check=ACCEPTS_EVERYTHING)

    report = run.report()
    assert "[PROVED]" in report
    assert "theorem foo : 1 + 1 = 2" in report


def test_the_report_says_not_proved_on_failure():
    run = prove("hard", formalizer=FakeFormalizer(), check=ACCEPTS_NOTHING, depth=0)
    assert "[NOT PROVED]" in run.report()


def test_every_attempt_is_recorded_for_inspection():
    run = prove("hard", formalizer=FakeFormalizer(), check=ACCEPTS_NOTHING, depth=0)

    assert [a.number for a in run.attempts] == list(range(1, len(run.attempts) + 1))
    assert any("formalise" in entry for entry in run.trace)
