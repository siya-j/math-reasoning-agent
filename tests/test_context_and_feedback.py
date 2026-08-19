"""Three limitations found by inspection, and the tests that pin the fixes.

  1. Synthesised lemmas were compiled STANDALONE, so any hole mentioning the
     goal's own objects could not elaborate — which is almost every hole on a
     real goal. Decomposition fired and achieved nothing.

  2. After a rejection the model chose its own next query, and the traces show
     what it chose: `"constant"`, `"deriv"`, `"abs"`, `"re"`. The error itself
     named what was missing and nothing used it.

  3. Retrieval put DEFINITIONS first. `"IsCyclic"` returned the definition and
     a constructor ahead of any lemma; the proof turned out to be
     `exact inferInstance`.

Every string quoted below is copied from a recorded run.
"""

import asyncio

import pytest

from math_v2.core import binders, diagnosis, log, proving, retrieval
from retrieval.loogle import Premise
from verifiers.lean_runner import LeanOutcome, LeanResult

# proofnet exercise_1_13a, verbatim.
GOAL = (
    "theorem exercise_1_13a {f : ℂ → ℂ} (Ω : Set ℂ) (a b : Ω) (h : IsOpen Ω)\n"
    "  (hf : DifferentiableOn ℂ f Ω) (hc : ∃ (c : ℝ), ∀ z ∈ Ω, (f z).re = c) :\n"
    "  f a = f b"
)
GROUP_GOAL = ("theorem subgroup_cyclic {G : Type*} [Group G] [IsCyclic G] "
              "(H : Subgroup G) : IsCyclic H")


def run(coro):
    return asyncio.run(coro)


def compiler(plan=()):
    seen = []

    async def run_lean(source):
        seen.append(source)
        for needle, outcome in plan:
            if needle in source:
                out = "declaration uses 'sorry'" if outcome is LeanOutcome.INCOMPLETE else ""
                return LeanResult(outcome, out)
        return LeanResult(LeanOutcome.ERRORS, "f.lean:1:1: error: unsolved goals")

    return run_lean, seen


@pytest.fixture
def workdir(tmp_path):
    log.clear(str(tmp_path))
    return str(tmp_path)


# ============================== 1. context-aware lemma synthesis
def test_a_hole_gets_the_binders_it_mentions():
    sig = ("{f : ℂ → ℂ} (Ω : Set ℂ) (a b : Ω) (h : IsOpen Ω) "
           "(hf : DifferentiableOn ℂ f Ω)")

    lemma = binders.lemma_signature("L", sig, "∀ x ∈ Ω, deriv f x = 0")

    assert "{f : ℂ → ℂ}" in lemma and "(Ω : Set ℂ)" in lemma
    assert lemma.endswith(": ∀ x ∈ Ω, deriv f x = 0")


def test_the_dependency_closure_is_transitive():
    """`(a b : Ω)` is useless without `(Ω : Set ℂ)`. A claim mentioning only
    `a` must still pull in `Ω`."""
    sig = "{f : ℂ → ℂ} (Ω : Set ℂ) (a b : Ω)"

    lemma = binders.lemma_signature("L", sig, "f a = f b")

    assert "(Ω : Set ℂ)" in lemma, "the type of a needed binder was dropped"
    assert "(a b : Ω)" in lemma


def test_instance_binders_come_along():
    """`[Group G]` names nothing, so no claim can reference it — and without it
    the lemma does not elaborate."""
    lemma = binders.lemma_signature(
        "L", "{G : Type*} [Group G] [IsCyclic G] (H : Subgroup G)", "IsCyclic H")

    assert "[Group G]" in lemma and "[IsCyclic G]" in lemma


def test_hypotheses_about_objects_in_scope_are_included():
    """Nothing in `∀ x ∈ Ω, deriv f x = 0` references `hf`, and without `hf`
    the claim is not provable."""
    sig = "{f : ℂ → ℂ} (Ω : Set ℂ) (hf : DifferentiableOn ℂ f Ω)"

    assert "(hf : DifferentiableOn ℂ f Ω)" in binders.lemma_signature(
        "L", sig, "∀ x ∈ Ω, deriv f x = 0")


def test_a_claim_needing_nothing_copies_nothing():
    """THE test against "just copy every binder". An arithmetic hole is a
    standalone lemma and must stay one."""
    sig = "{f : ℂ → ℂ} (Ω : Set ℂ) (hf : DifferentiableOn ℂ f Ω)"

    assert binders.lemma_signature("L", sig, "1 + 1 = 2") == "theorem L : 1 + 1 = 2"


def test_pure_data_binders_are_not_dragged_in_as_hypotheses():
    """`(a b : Ω)` is data over an in-scope type, not a fact about it."""
    sig = "(Ω : Set ℂ) (a b : Ω) (h : IsOpen Ω)"

    assert "(a b : Ω)" not in binders.lemma_signature("L", sig, "IsOpen Ω")


def test_synthesis_uses_the_context_on_the_real_goal(workdir):
    """End to end: the lemma compiled for a hole of exercise_1_13a carries the
    goal's binders."""
    skeleton = ("by\n  have hd : ∀ x ∈ Ω, deriv f x = 0 := by sorry\n"
                "  exact foo hd")
    run_lean, seen = compiler([("sorry", LeanOutcome.INCOMPLETE)])

    run(proving.try_skeleton(workdir, GOAL, skeleton, run_lean, 4))

    lemma_source = [s for s in seen if "mra_lemma_1" in s or "deriv f x = 0" in s]
    assert lemma_source, "the hole was never compiled"
    assert "(Ω : Set ℂ)" in lemma_source[-1], "the lemma was compiled standalone"


def test_the_assembled_citation_can_supply_hypotheses(workdir):
    """A lemma with hypotheses cannot be closed by a bare `exact`. The fill has
    to discharge them from the parent's context."""
    skeleton = ("by\n  have hd : ∀ x ∈ Ω, deriv f x = 0 := by sorry\n"
                "  exact foo hd")
    # `rename_goal` renames the LAST declaration, so the probe compiled for the
    # hole is called `mra_goal` like everything else — match on the claim.
    run_lean, seen = compiler([
        ("sorry", LeanOutcome.INCOMPLETE),
        ("deriv f x = 0", LeanOutcome.COMPILED),
    ])

    run(proving.try_skeleton(workdir, GOAL, skeleton, run_lean, 4))

    assembled = [s for s in seen if "apply mra_lemma_1" in s]
    assert assembled, "the citation could not supply the lemma's hypotheses"
    assert "assumption" in assembled[-1]


# ============================== 2. the error becomes the next query
@pytest.mark.parametrize("detail,expected", [
    ("2:58: error: Unknown constant `Complex.abs`", '"abs"'),
    ("6:31: error: Unknown identifier `Basis`", '"Basis"'),
    ("2:58: error: failed to synthesize instance of type class DecidableEq α",
     '"DecidableEq"'),
])
def test_the_query_is_taken_from_the_error(detail, expected):
    assert diagnosis.retrieval_query(detail) == expected


def test_an_unsolved_goal_becomes_a_conclusion_pattern():
    """`|- ` matches what a lemma CONCLUDES, which is what closes a goal."""
    query = diagnosis.retrieval_query(
        "f.lean:4:2: error: unsolved goals\n\nStill to prove:\n⊢ IsCyclic ↥H")

    assert query.startswith("|- ")
    assert "IsCyclic" in query


@pytest.mark.parametrize("detail", [
    "21:29: error: Application type mismatch: The argument",
    "4:2: warning: aesop: failed to prove the goal after exhaustive search.",
    "2:24: error: unexpected token 'in'; expected ','",
    "",
])
def test_no_query_is_invented_where_none_follows(detail):
    """A wrong query costs a lookup and puts noise in front of the model. "" is
    the right answer for a mismatch, a tactic failure and a syntax error."""
    assert diagnosis.retrieval_query(detail) == ""


class FakeSearch:
    def __init__(self, premises=()):
        self.premises = list(premises)
        self.queries = []

    def search_with_suggestions(self, query, limit=None):
        self.queries.append(query)
        return list(self.premises), []


def test_a_rejection_runs_the_derived_query_and_returns_what_it_found(workdir):
    """Not an instruction to search — the search itself, done."""
    search = FakeSearch([Premise(name="Complex.abs_apply", type=" : ‖z‖ = z.abs",
                                 module="Mathlib.Analysis")])

    async def run_lean(source):
        return LeanResult(LeanOutcome.ERRORS,
                          "2:58: error: Unknown constant `Complex.abs`")

    result = run(proving.try_proof(workdir, GOAL, "by exact Complex.abs",
                                   run_lean, search))

    assert search.queries == ['"abs"']
    assert result["outputs"]["retrieved"] == ["Complex.abs_apply"]
    assert "SEARCHED FOR YOU" in result["message"]
    assert "‖z‖ = z.abs" in result["message"], "the signature was not shown"


def test_the_retrieved_premises_are_kept_for_the_tactic_ladder(workdir):
    search = FakeSearch([Premise(name="Complex.abs_apply", type=" : 1 = 1",
                                 module="Mathlib.Analysis")])

    async def run_lean(source):
        return LeanResult(LeanOutcome.ERRORS, "error: Unknown constant `Complex.abs`")

    run(proving.try_proof(workdir, GOAL, "by exact Complex.abs", run_lean, search))

    assert "Complex.abs_apply" in [p.name for p in proving._premises(workdir)]


def test_only_one_query_is_run_per_rejection(workdir):
    """Bounded by MAX_LEAN_CALLS without any new counter."""
    search = FakeSearch([Premise(name="X", type=" : 1 = 1")])

    async def run_lean(source):
        return LeanResult(LeanOutcome.ERRORS, "error: Unknown constant `Complex.abs`")

    run(proving.try_proof(workdir, GOAL, "by exact A", run_lean, search))

    assert len(search.queries) == 1


def test_no_query_runs_when_the_error_implies_none(workdir):
    search = FakeSearch()

    async def run_lean(source):
        return LeanResult(LeanOutcome.ERRORS, "21:29: error: type mismatch")

    run(proving.try_proof(workdir, GOAL, "by exact A", run_lean, search))

    assert search.queries == []


def test_a_broken_search_does_not_break_a_rejection(workdir):
    class Boom:
        def search_with_suggestions(self, *a, **k):
            raise RuntimeError("loogle down")

    async def run_lean(source):
        return LeanResult(LeanOutcome.ERRORS, "error: Unknown constant `Complex.abs`")

    result = run(proving.try_proof(workdir, GOAL, "by exact A", run_lean, Boom()))

    assert result["outputs"]["accepted"] is False


# ============================== 3. lemmas rank above definitions
def test_a_definition_is_ranked_below_the_lemmas():
    """`"IsCyclic"` returned the definition first and spent the visible slots
    on things that cannot close a goal."""
    ranked = retrieval.rank([
        Premise(name="IsCyclic", type=" (α : Type u) : Prop"),
        Premise(name="IsCyclic.exists_zpow_surjective", type=" : ∃ g, ∀ x, x ∈ _"),
        Premise(name="isCyclic_of_subsingleton", type=" : IsCyclic α"),
    ])

    assert [p.name for p in ranked][-1] == "IsCyclic"
    assert ranked[0].name == "IsCyclic.exists_zpow_surjective"


def test_the_lemma_order_loogle_chose_is_preserved():
    """Loogle's relevance ranking is better than anything invented here; this
    only moves definitions down."""
    ranked = retrieval.rank([
        Premise(name="first_lemma", type=" : 1 = 1"),
        Premise(name="second_lemma", type=" : 2 = 2"),
    ])

    assert [p.name for p in ranked] == ["first_lemma", "second_lemma"]


def test_a_repeated_name_appears_once():
    """The same declaration can match several rungs of the query ladder."""
    ranked = retrieval.rank([
        Premise(name="dup", type=" : 1 = 1"),
        Premise(name="dup", type=" : 1 = 1"),
        Premise(name="other", type=" : 2 = 2"),
    ])

    assert [p.name for p in ranked] == ["dup", "other"]


def test_container_plumbing_is_filtered_from_retrieval():
    """MEASURED on proofnet `exercise_1_18a`: `"inner"` — the inner product —
    returned `Array.Matcher.Iterator.inner` and `Batteries.HashMap.inner`.
    `Batteries.Tactic` was too narrow a prefix to catch them."""
    noisy = [
        Premise(name="Array.Matcher.Iterator.inner", module="Array.Match"),
        Premise(name="Batteries.HashMap.inner", module="Batteries.HashMap"),
    ]
    real = Premise(name="inner_self_eq_norm_sq", type=" : ⟪x, x⟫ = ‖x‖ ^ 2",
                   module="Mathlib.Analysis.InnerProductSpace.Basic")

    kept, dropped = retrieval.drop_noise(noisy + [real])

    assert [p.name for p in kept] == ["inner_self_eq_norm_sq"]
    assert dropped == 2


def test_a_list_of_only_noise_is_not_emptied():
    """Unchanged behaviour: an empty result would look like a failed search,
    and the agent cannot tell those apart. Seeing the junk is the signal."""
    noisy = [Premise(name="Array.Matcher.Iterator.inner", module="Array.Match")]

    kept, dropped = retrieval.drop_noise(noisy)

    assert len(kept) == 1 and dropped == 0
