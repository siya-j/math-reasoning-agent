"""Four deterministic changes, each from one measured failure in the 4-goal run.

    1_13a, 1_13c, 1_26   reported suspect with a prose counterexample and never
                         called `try_refutation` -> the negation is now built
                         for the model and asked for at the exit
    1_19b                formalised to `: True`, which `by trivial` closes ->
                         a conclusion of `True` is refused
    1_13c                spent a compile on `exact DifferentiableOn`, which is
                         a DEFINITION -> definitions leave the tactic ladder
    1_26                 submitted the same skeleton three times -> the repeat
                         guard now covers skeletons

Nothing here needs a model or a compiler. The point of each change is that the
answer was knowable from the text.
"""

import asyncio

import pytest

from math_v2.core import log, proving, verdict
from pipeline.tactics import cheap_attempt, provides_a_proof
from retrieval.loogle import Premise
from verifiers.lean_runner import LeanOutcome, LeanResult

# The real statements, copied from eval/results/proofnet-4-refuted.json.
EX_1_13A = (
    "theorem exercise_1_13a {f : ℂ → ℂ} (Ω : Set ℂ) (a b : Ω) (h : IsOpen Ω)\n"
    "  (hf : DifferentiableOn ℂ f Ω) (hc : ∃ (c : ℝ), ∀ z ∈ Ω, (f z).re = c) :\n"
    "  f a = f b"
)
EX_1_26 = (
    "theorem exercise_1_26\n"
    "  (f F₁ F₂ : ℂ → ℂ) (Ω : Set ℂ) (h1 : IsOpen Ω) (h2 : IsConnected Ω)\n"
    "  (hF₁ : DifferentiableOn ℂ F₁ Ω) (hF₂ : DifferentiableOn ℂ F₂ Ω)\n"
    "  (hdF₁ : ∀ x ∈ Ω, deriv F₁ x = f x) (hdF₂ : ∀ x ∈ Ω, deriv F₂ x = f x)\n"
    "  : ∃ c : ℂ, ∀ x, F₁ x = F₂ x + c"
)
# 1_19b, as the model finally submitted it. It elaborates and says nothing.
EX_1_19B_COLLAPSED = (
    "theorem test (z : ℂ) (s : ℕ → ℂ) "
    "(h : s = (λ n => Finset.sum (Finset.range n) (λ i => z ^ i))) : True"
)


def run(coro):
    return asyncio.run(coro)


def compiler(outcome=LeanOutcome.COMPILED, output=""):
    seen = []

    async def run_lean(source):
        seen.append(source)
        return LeanResult(outcome, output)

    return run_lean, seen


@pytest.fixture
def workdir(tmp_path):
    log.clear(str(tmp_path))
    return str(tmp_path)


# ------------------------------------------- 1. the negation is built, not asked
def test_the_negation_keeps_the_binders_exactly_as_written():
    """Implicit `{f : ℂ → ℂ}` stays implicit. Lean's `∀` takes theorem binders
    verbatim, so no rewriting is needed — and every rewrite is a chance to be
    wrong about a statement the model already struggled to produce."""
    negation = proving.negation_of(EX_1_13A)

    assert negation.startswith("theorem exercise_1_13a_refutation : ¬ (∀ ")
    assert "{f : ℂ → ℂ}" in negation
    assert "(hc : ∃ (c : ℝ), ∀ z ∈ Ω, (f z).re = c)" in negation
    assert negation.rstrip().endswith("f a = f b)")


def test_an_existential_conclusion_is_not_cut_in_half():
    """THE parsing trap. `∃ c : ℂ, ...` puts a colon at bracket depth 0, so
    taking the LAST top-level colon — which is what `conclusion_of` does,
    correctly, for its own job — splits the conclusion and yields
    `¬ (∀ ... : ∃ c, ℂ, ...)`, which is not Lean."""
    negation = proving.negation_of(EX_1_26)

    assert "∃ c : ℂ, ∀ x, F₁ x = F₂ x + c)" in negation
    assert ", ℂ," not in negation


def test_a_statement_with_no_binders_needs_no_forall():
    assert proving.negation_of("theorem t : 2 + 2 = 5") == (
        "theorem t_refutation : ¬ (2 + 2 = 5)"
    )


def test_a_statement_that_cannot_be_parsed_yields_nothing():
    """"" rather than a guess. A malformed negation would be refused by the
    negation lint anyway, but silently offering nonsense is worse than silence."""
    assert proving.negation_of("not a theorem at all") == ""
    assert proving.negation_of("") == ""


def test_try_refutation_builds_the_negation_when_none_is_given(workdir):
    log.set_goal(workdir, EX_1_13A)
    run_lean, seen = compiler()

    result = run(proving.try_refutation(workdir, "", "by simp", run_lean))

    assert result["outputs"]["refuted"] is True
    # `rename_goal` renames the declaration, as it does for every other
    # attempt, so the assertion is on the negated CLAIM rather than the name.
    assert "¬ (∀ {f : ℂ → ℂ}" in seen[0]
    assert seen[0].rstrip().endswith("f a = f b) := by simp")


# ------------------------------------- 2. the exit asks for the counterexample
def test_a_proof_attempt_alone_no_longer_earns_the_suspect_exit(workdir):
    """All three suspect reports in the 4-goal run had a counterexample in
    prose and none in Lean. The tool existed; nothing ever put the model in
    front of it."""
    log.set_goal(workdir, EX_1_13A)
    log.append(workdir, log.Record(kind=log.PROOF, statement=EX_1_13A,
                                   proof="by aesop", status=log.FALSE))

    refusal = verdict.suspect_refusal(workdir)

    assert "try_refutation" in refusal
    assert "exercise_1_13a_refutation" in refusal, "the negation was not handed over"


def test_a_failed_refutation_still_earns_the_exit(workdir):
    """The requirement is that the counterexample reached the compiler. Making
    it a requirement to SUCCEED would suppress real findings on a benchmark
    that is 31.8% broken."""
    log.set_goal(workdir, EX_1_13A)
    log.append(workdir, log.Record(kind=log.PROOF, statement=EX_1_13A,
                                   proof="by aesop", status=log.FALSE))
    run(proving.try_refutation(
        workdir, "", "by simp", compiler(LeanOutcome.ERRORS, "error")[0]))

    assert verdict.suspect_refusal(workdir) == ""


# ------------------------------------------------ 3. no semantic collapse
def test_a_conclusion_of_True_is_refused_without_compiling(workdir):
    """1_19b. It elaborates, `by trivial` closes it, and the run scored a
    formalisation success for a claim that had been thrown away — which is
    worse than the failure it replaced, because it is invisible."""
    run_lean, seen = compiler()

    result = run(proving.check_statement(workdir, EX_1_19B_COLLAPSED, run_lean))

    assert result["error"] == "trivial_conclusion"
    assert seen == [], "a statement saying nothing was compiled"


def test_the_trivial_statement_cannot_get_in_by_another_door(workdir):
    """`check_statement` is not the only entry point, so the lint is on the
    tools that would otherwise accept a proof of it."""
    run_lean, seen = compiler()

    proof = run(proving.try_proof(workdir, EX_1_19B_COLLAPSED, "by trivial", run_lean))
    skeleton = run(proving.try_skeleton(workdir, EX_1_19B_COLLAPSED,
                                        "by trivial", run_lean))

    assert proof["error"] == "trivial_conclusion"
    assert skeleton["error"] == "trivial_conclusion"
    assert seen == []


def test_a_real_statement_is_untouched(workdir):
    """The lint must block collapse, not block work."""
    run_lean, seen = compiler()

    result = run(proving.check_statement(workdir, EX_1_13A, run_lean))

    assert result["outputs"]["elaborates"] is True
    assert len(seen) == 1


# --------------------------------------- 4. the ladder tries only real lemmas
def test_a_definition_never_reaches_the_tactic_ladder():
    """1_13c spent a compile on nine forms of `exact DifferentiableOn`. It is
    the goal's own predicate — there is no proof in it to `exact`."""
    definition = Premise(name="DifferentiableOn",
                         type=" (𝕜 : Type u) (f : E → F) (s : Set E) : Prop")

    assert not provides_a_proof(definition)
    assert "DifferentiableOn" not in cheap_attempt([definition])


def test_a_real_lemma_still_reaches_it():
    lemma = Premise(name="DifferentiableOn.mono",
                    type=" (h : DifferentiableOn 𝕜 f t) (st : s ⊆ t) : "
                         "DifferentiableOn 𝕜 f s")

    assert provides_a_proof(lemma)
    assert "exact DifferentiableOn.mono" in cheap_attempt([lemma])


def test_a_premise_with_no_type_is_kept():
    """Loogle omits `type` on some hits. Dropping a premise for want of
    metadata would lose real lemmas to make the ladder tidier."""
    assert provides_a_proof(Premise(name="foo_bar"))


def test_the_filter_runs_before_the_premise_limit():
    """Definitions rank FIRST in a name search, so filtering after the limit
    would spend the whole ladder budget on them."""
    premises = [Premise(name=f"Def{i}", type=" : Prop") for i in range(5)]
    premises.append(Premise(name="real_lemma", type=" : 1 = 1"))

    assert "exact real_lemma" in cheap_attempt(premises, limit=2)


# ------------------------------------------- 5. a repeated skeleton is refused
def test_the_same_skeleton_is_not_compiled_twice(workdir):
    """1_26 submitted the same decomposition three times, with the `sorry` in
    the same place. The repeat guard was reading `kind == PROOF` only."""
    run_lean, seen = compiler(LeanOutcome.ERRORS, "f.lean:1:1: error: nope")
    skeleton = "by\n  have h : True := by sorry\n  exact h"

    run(proving.try_skeleton(workdir, EX_1_13A, skeleton, run_lean))
    again = run(proving.try_skeleton(workdir, EX_1_13A, skeleton, run_lean))

    assert again["error"] == "duplicate_attempt"
    assert len(seen) == 1, "the duplicate reached the compiler"


def test_a_different_skeleton_is_compiled(workdir):
    """The guard must stop repetition, not stop decomposition."""
    run_lean, seen = compiler(LeanOutcome.ERRORS, "f.lean:1:1: error: nope")

    run(proving.try_skeleton(workdir, EX_1_13A,
                             "by\n  have h : True := by sorry\n  exact h", run_lean))
    run(proving.try_skeleton(workdir, EX_1_13A,
                             "by\n  have g : 1 = 1 := by sorry\n  simp", run_lean))

    assert len(seen) == 2
