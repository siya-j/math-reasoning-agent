"""Reading what Lean understood, not what the source looks like.

WRITTEN AFTER THE AUDIT GOT IT WRONG. The round-trip check called four
genuinely broken ProofNet statements faithful, because it back-translated
the SOURCE. In Lean the source and the elaborated term differ exactly where
the bugs are:

    Icc 0 1   ->  Icc (0 : ℕ) (1 : ℕ)      a two-point set, not [0,1]
    finrank V ->  finrank applied to V AS THE RING

A model shown the source describes what a mathematician would assume. That
assumption is the bug.
"""

import pytest

from eval import elaborate as E
from eval.elaborate import Elaboration, elaborate, name_of, source_for
from verifiers.lean_runner import LeanOutcome, LeanResult

PREAMBLE = "import Mathlib"
STATEMENT = "theorem exercise_1 (n : Nat) : n + 0 = n"


def lean(outcome, output):
    def run(source, timeout=None):
        return LeanResult(outcome=outcome, output=output)
    return run


# ----------------------------------------------------------------- the file
def test_the_name_is_taken_from_the_declaration():
    assert name_of("theorem exercise_1 (n : Nat) : True") == "exercise_1"
    assert name_of("lemma foo {G : Type*} : True") == "foo"
    assert name_of("example : True") == ""


def test_the_source_asks_lean_to_print_the_type():
    source = source_for(PREAMBLE, STATEMENT)
    assert "#check @exercise_1" in source
    assert ":= sorry" in source
    assert "pp.numericTypes" in source, (
        "without this, Icc 0 1 prints as Icc 0 1 and the audit learns nothing"
    )


# ------------------------------------------------------------- what it reads
def test_the_printed_type_is_extracted():
    output = (
        "Claim.lean:4:8: warning: declaration uses `sorry`\n"
        "exercise_1 : ∀ (n : ℕ), n + 0 = n\n"
    )
    result = elaborate(PREAMBLE, STATEMENT, run=lean(LeanOutcome.INCOMPLETE, output))
    assert result.usable
    assert result.text == "exercise_1 : ∀ (n : ℕ), n + 0 = n"


def test_a_multi_line_type_is_kept_whole():
    output = (
        "Claim.lean:4:8: warning: declaration uses `sorry`\n"
        "exercise_1 : ∀ (f : ℕ → ℝ),\n"
        "  (∀ x, f x = 0) → True\n"
    )
    result = elaborate(PREAMBLE, STATEMENT, run=lean(LeanOutcome.INCOMPLETE, output))
    assert "∀ (f : ℕ → ℝ)" in result.text
    assert "(∀ x, f x = 0) → True" in result.text


# -------------------------------------------- what the compiler settles alone
def test_a_statement_that_does_not_elaborate_is_not_usable():
    result = elaborate(
        PREAMBLE, STATEMENT,
        run=lean(LeanOutcome.ERRORS,
                 "Claim.lean:3:2: error: unknown identifier 'Frobnicate'"),
    )
    assert not result.usable
    assert "does not elaborate" in result.problem


def test_sorry_in_the_TYPE_is_a_broken_statement():
    """Different from the expected `declaration uses sorry` warning, which
    comes from the proof we deliberately left out. A `sorry` in the TYPE
    means Lean could not understand part of the STATEMENT.

    MEASURED: Munkres_exercise_25_9 ends in `→ sorry` because autoImplicit
    silently bound an unknown identifier as a free variable.
    """
    output = (
        "Claim.lean:4:8: warning: declaration uses `sorry`\n"
        "@exercise_1 : {IsNormalSubgroup : x} → (C : Set G) → sorry\n"
    )
    result = elaborate(PREAMBLE, STATEMENT, run=lean(LeanOutcome.INCOMPLETE, output))
    assert not result.usable
    assert "`sorry`" in result.problem


def test_an_unnamed_declaration_is_refused():
    assert not elaborate(PREAMBLE, "example : True").usable


@pytest.mark.parametrize(
    "outcome, expected",
    [(LeanOutcome.UNAVAILABLE, "no Lean"), (LeanOutcome.TIMEOUT, "timed out")],
)
def test_lean_being_absent_is_reported_not_guessed(outcome, expected):
    result = elaborate(PREAMBLE, STATEMENT, run=lean(outcome, ""))
    assert not result.usable
    assert expected in result.problem


def test_a_crash_in_lean_is_not_a_crash_here():
    def explode(source, timeout=None):
        raise RuntimeError("lake is on fire")

    result = elaborate(PREAMBLE, STATEMENT, run=explode)
    assert not result.usable
    assert "lake is on fire" in result.problem


# --------------------------------------------------- how the round trip uses it
def test_the_model_reads_the_elaborated_form_not_the_source():
    """THE fix. Given the source, a model describes the unit interval; given
    the elaborated form, it can see the naturals."""
    from eval import roundtrip

    seen = []

    class Model:
        def invoke(self, prompt):
            seen.append(prompt)
            return "VERDICT: mismatch\nWHY: the domain is the naturals."

    roundtrip.assess(
        "g", "suspect_statement",
        formal="theorem t (f : Icc 0 1 -> R) : True",
        informal="A statement about the unit interval.",
        model=Model(),
        elaboration=Elaboration(True, "t : (f : Icc (0 : ℕ) (1 : ℕ) → ℝ) → True"),
    )
    assert "Icc (0 : ℕ) (1 : ℕ)" in seen[0]
    assert "theorem t (f : Icc 0 1" not in seen[0]


def test_a_broken_statement_is_settled_without_asking_the_model():
    """The compiler's verdict is bought with a compilation. The model's is
    not, so it is not consulted once the compiler has ruled."""
    from eval import roundtrip

    class Model:
        def invoke(self, prompt):
            raise AssertionError("the model should not have been asked")

    result = roundtrip.assess(
        "g", "suspect_statement", "theorem t : True", "Something.",
        model=Model(),
        elaboration=Elaboration(False, problem="does not elaborate"),
    )
    assert result.verdict == roundtrip.BROKEN
    assert result.why == "does not elaborate"


def test_broken_does_not_go_on_the_reading_list():
    """It CONFIRMS the exclusion rather than questioning it. There is
    nothing for a person to adjudicate."""
    from eval.roundtrip import BROKEN, MATCH, Assessment

    assert not Assessment("g", "suspect_statement", BROKEN, "").needs_a_human
    assert Assessment("g", "suspect_statement", MATCH, "").needs_a_human


def test_without_an_elaboration_the_source_is_still_used():
    """--no-elaborate must keep working, for a machine with no Lean."""
    from eval import roundtrip

    seen = []

    class Model:
        def invoke(self, prompt):
            seen.append(prompt)
            return "VERDICT: match\nWHY: same."

    roundtrip.assess("g", "refuted", "theorem t : True", "Something.", Model())
    assert "theorem t : True" in seen[0]
