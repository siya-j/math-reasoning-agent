"""Turning compiler feedback into a DIFFERENT next action. No model, no Lean.

WHAT THE 4-GOAL RUN ACTUALLY SHOWED
-----------------------------------
The agent's mathematics was often right. On `exercise_1_13a` it named the
missing hypothesis correctly — Ω is assumed open, not connected — and then
submitted:

    by aesop
    by rcases hc with ⟨c, hc⟩; have h1 : ∀ z ∈ Ω, (f z).re = c := hc; trivial

and on `exercise_1_13c`, the 30-tactic ladder followed by `by rfl`. The
understanding never reached the Lean. Three mechanisms were missing, and all
three are decidable from text:

  * the rejection said what KIND of failure it was; nothing acted on that
  * `try_standard_tactics` had already run every generic closer, and separate
    compiles were spent submitting them again one at a time
  * retrieval only ever saw the words the model typed, never the goal's shape,
    although the machinery to build queries from the shape already existed

These tests pin the three. They assert CONTROLLER DECISIONS — what the agent is
told, what reaches the compiler — never model output.
"""

import asyncio

import pytest

from math_v2.core import diagnosis, log, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = "theorem mra_goal (Ω : Set ℂ) (h : IsOpen Ω) : f a = f b"

UNSOLVED = (
    "Lean rejected the proof.\n4:2: error: unsolved goals\n\n"
    "Still to prove:\n⊢ f ↑a = f ↑b"
)
UNKNOWN = "2:58: error: Unknown constant `Complex.abs`"
MISMATCH = "21:29: error: Application type mismatch: The argument"
INSTANCE = "2:58: error: failed to synthesize instance of type class"
SYNTAX = "2:24: error: unexpected token 'in'; expected ','"
AESOP = "4:2: warning: aesop: failed to prove the goal after exhaustive search."


def run(coro):
    return asyncio.run(coro)


def compiler(outcome=LeanOutcome.ERRORS, output=UNSOLVED):
    seen = []

    async def run_lean(source):
        seen.append(source)
        return LeanResult(outcome, output)

    return run_lean, seen


@pytest.fixture
def workdir(tmp_path):
    log.clear(str(tmp_path))
    return str(tmp_path)


# ------------------------------------------- 1. errors are classified, not read
@pytest.mark.parametrize("detail,expected", [
    (UNKNOWN, diagnosis.UNKNOWN_IDENTIFIER),
    (INSTANCE, diagnosis.TYPECLASS),
    (MISMATCH, diagnosis.TYPE_MISMATCH),
    (SYNTAX, diagnosis.SYNTAX),
    (AESOP, diagnosis.TACTIC_FAILED),
    (UNSOLVED, diagnosis.UNSOLVED),
    ("", diagnosis.UNCLASSIFIED),
])
def test_every_failure_seen_in_the_run_is_classified(detail, expected):
    """All six strings are copied from eval/results/proofnet-4-refuted.json."""
    assert diagnosis.classify(detail) is expected


def test_each_failure_leads_somewhere_different():
    """A classification that produced the same advice every time would be a
    relabelling of "it failed", which the agent already knew."""
    actions = {diagnosis.next_action(d)
               for d in (UNKNOWN, INSTANCE, MISMATCH, SYNTAX, UNSOLVED)}

    assert len(actions) == 5
    assert all(actions)


def test_an_unknown_identifier_is_sent_to_search_not_to_a_guess():
    assert "search_mathlib" in diagnosis.next_action(UNKNOWN)


def test_a_type_mismatch_is_sent_to_the_signature():
    assert "signature" in diagnosis.next_action(MISMATCH)


def test_a_tactic_failure_is_not_sent_to_another_tactic():
    """`aesop` failing means the goal is not aesop-shaped. Reaching for `simp`
    next is the exact loop this exists to break."""
    action = diagnosis.next_action(AESOP)

    assert "do not try a different generic tactic" in action.lower()
    assert "try_skeleton" in action


def test_the_instruction_reaches_the_agent_with_the_rejection(workdir):
    run_lean, _ = compiler(output=UNKNOWN)

    result = run(proving.try_proof(workdir, GOAL, "by exact Complex.abs", run_lean))

    assert result["outputs"]["failure"] == diagnosis.UNKNOWN_IDENTIFIER
    assert "WHAT THIS MEANS" in result["message"]
    assert "search_mathlib" in result["message"]


# ------------------------------- 2. attempts must differ in KIND, not in text
@pytest.mark.parametrize("proof,expected", [
    ("by aesop", diagnosis.GENERIC),
    ("by\n  simp", diagnosis.GENERIC),
    ("by norm_num <;> simp", diagnosis.GENERIC),
    ("by\n  first\n    | rfl\n    | simp", diagnosis.LADDER),
    ("by exact Nat.exists_infinite_primes n", diagnosis.CITATION),
    ("by\n  have h : deriv f = 0 := by simp\n  exact foo h", diagnosis.STRUCTURED),
    ("by\n  rcases hc with ⟨c, hc⟩\n  trivial", diagnosis.STRUCTURED),
])
def test_attempts_are_sorted_by_kind(proof, expected):
    assert diagnosis.strategy_of(proof) is expected


def test_a_second_generic_closer_is_refused_without_compiling(workdir):
    """`by aesop` after the ladder cannot learn anything: the ladder ran aesop,
    in the same file, and Lean said no."""
    run_lean, seen = compiler()

    run(proving.try_proof(workdir, GOAL, "by simp", run_lean))
    second = run(proving.try_proof(workdir, GOAL, "by aesop", run_lean))

    assert second["error"] == "generic_exhausted"
    assert len(seen) == 1, "a closer the ladder already ran was compiled again"


def test_the_refusal_offers_three_different_routes_out(workdir):
    """A refusal with no way forward just burns the remaining turns."""
    run_lean, _ = compiler()
    run(proving.try_proof(workdir, GOAL, "by simp", run_lean))

    message = run(proving.try_proof(workdir, GOAL, "by rfl", run_lean))["message"]

    assert "search_mathlib" in message
    assert "try_skeleton" in message
    assert "try_refutation" in message


def test_a_real_argument_is_never_refused_as_generic(workdir):
    """The rule must stop repetition, not stop proving. A `have` chain is an
    argument even when its last step is `simp`."""
    run_lean, seen = compiler()

    run(proving.try_proof(workdir, GOAL, "by aesop", run_lean))
    result = run(proving.try_proof(
        workdir, GOAL,
        "by\n  have hd : deriv f = 0 := by simp\n  exact const_of_deriv_zero hd",
        run_lean))

    assert result.get("error") != "generic_exhausted"
    assert len(seen) == 2


def test_the_first_generic_attempt_is_always_allowed(workdir):
    """Cheap fallbacks are still worth one compile. They are just not a
    strategy."""
    run_lean, seen = compiler()

    result = run(proving.try_proof(workdir, GOAL, "by aesop", run_lean))

    assert result.get("error") is None
    assert len(seen) == 1


# --------------------------- 3. retrieval starts from the goal, not from a word
class FakeSearch:
    """Stands in for LoogleSearch. Records what it was asked for."""

    def __init__(self, premises=(), boom=False):
        self.premises = list(premises)
        self.asked = []
        self.boom = boom

    def premises_for(self, statement):
        if self.boom:
            raise RuntimeError("loogle is down")
        self.asked.append(statement)
        return self.premises


def premise(name, type_=" : 1 = 1", module="Mathlib.Analysis"):
    from retrieval.loogle import Premise

    return Premise(name=name, type=type_, module=module)


def test_the_goal_shape_ladder_runs_the_moment_the_statement_elaborates(workdir):
    """`retrieval.loogle.premises_for` builds queries from the STATEMENT —
    hypothesis shape, then conclusion patterns. It was tested, it was better
    than bare names, and nothing on the math_v2 path ever called it."""
    search = FakeSearch([premise("is_const_of_deriv_eq_zero")])
    run_lean, _ = compiler(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")

    result = run(proving.check_statement(workdir, GOAL, run_lean, search))

    assert search.asked == [GOAL], "the ladder was not run off the goal"
    assert result["outputs"]["premises"] == ["is_const_of_deriv_eq_zero"]


def test_the_seeded_premises_are_shown_with_their_signatures(workdir):
    """Names alone are what produced `exact DifferentiableOn`. The signature is
    what says whether a lemma applies."""
    search = FakeSearch([premise("foo", type_=" (h : IsPreconnected s) : f = g")])
    run_lean, _ = compiler(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")

    message = run(proving.check_statement(workdir, GOAL, run_lean, search))["message"]

    assert "IsPreconnected" in message, "the signature was not shown"
    assert "Read the signatures" in message


def test_seeded_premises_are_available_to_the_tactic_ladder(workdir):
    """They go into the same store `search_mathlib` writes to, so the ladder
    and later prompts both see them without another lookup."""
    search = FakeSearch([premise("real_lemma")])
    run_lean, _ = compiler(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")

    run(proving.check_statement(workdir, GOAL, run_lean, search))

    assert [p.name for p in proving._premises(workdir)] == ["real_lemma"]


def test_machinery_is_filtered_out_of_the_seed(workdir):
    search = FakeSearch([premise("Std.Sat.AIG.getConstant", module="Std.Sat.AIG"),
                         premise("real_lemma")])
    run_lean, _ = compiler(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")

    result = run(proving.check_statement(workdir, GOAL, run_lean, search))

    assert result["outputs"]["premises"] == ["real_lemma"]


def test_a_broken_search_never_takes_down_a_run(workdir):
    """Retrieval is an optimisation. Without it the model guesses names, which
    is worse and not fatal."""
    run_lean, _ = compiler(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")

    result = run(proving.check_statement(workdir, GOAL, run_lean, FakeSearch(boom=True)))

    assert result["outputs"]["elaborates"] is True


def test_nothing_is_seeded_when_the_statement_does_not_elaborate(workdir):
    """Querying off a broken signature would spend the lookup on noise."""
    search = FakeSearch([premise("real_lemma")])
    run_lean, _ = compiler(LeanOutcome.ERRORS, UNKNOWN)

    run(proving.check_statement(workdir, GOAL, run_lean, search))

    assert search.asked == []
