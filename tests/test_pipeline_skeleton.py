"""Offline tests for `pipeline/skeleton.py`'s pure string manipulation.

Nothing here needs a model or a compiler — the module's own docstring says
so, and these are exactly the gap that let a real bug ship silently: every
existing test of `_try_skeleton` fakes `structure_check` (a substring check,
or a constant), so nothing ever inspected what `fill_hole` actually PRODUCES.
"""

from pipeline.skeleton import bare_tactic, fill_hole, hole_claims, hole_count


# --------------------------------------------------------------- hole_count
def test_hole_count_counts_standalone_sorry_only():
    assert hole_count("have h : True := by sorry\nexact h") == 1


def test_a_name_containing_sorry_is_not_a_hole():
    assert hole_count("theorem sorryless : True := trivial") == 0


# ---------------------------------------------------------------- fill_hole
def test_a_single_line_replacement_is_unchanged():
    """The common case — `exact foo`, or one alternative on one line — never
    needed re-indenting, and must not be touched now that others do."""
    result = fill_hole("have h : True := by sorry", 0, "exact trivial_lemma")
    assert result == "have h : True := by exact trivial_lemma"


def test_only_the_indexed_hole_changes():
    proof = "have h1 : True := by sorry\nhave h2 : True := by sorry"
    result = fill_hole(proof, 1, "trivial")
    assert "h1 : True := by sorry" in result
    assert "h2 : True := by trivial" in result


def test_a_multiline_replacement_is_indented_to_the_holes_own_column():
    """MEASURED, `hard-sophie-germain` (via the `pipeline.prover` call site
    this exists for): a multi-line filler landing at column 0 -- SHALLOWER
    than the `have` line the hole sits inside -- silently ended the
    enclosing tactic block right there, no matter what the filler's own
    tactics said. Every line past the first must land at the hole's own
    column; a bullet nested one level deeper in the filler must stay one
    level deeper here too."""
    skeleton = "  have h1 : ¬ P n := by sorry\n  exact foo h1"
    filler = "rcases n with _ | n\n· contradiction\n· simp"

    result = fill_hole(skeleton, 0, filler)
    lines = result.splitlines()

    have_line = next(l for l in lines if "rcases" in l)
    sibling_bullet = next(l for l in lines if l.strip() == "· contradiction")

    def col(line):
        return len(line) - len(line.lstrip(" "))

    hole_column = have_line.index("rcases")
    assert col(sibling_bullet) == hole_column, (
        "a line past the first must land at the hole's own column, not at "
        "whatever column the filler itself was written at"
    )


def test_an_empty_line_in_a_multiline_replacement_stays_empty():
    """A blank separator line must not become a line of trailing spaces."""
    result = fill_hole("h := by sorry", 0, "tac1\n\ntac2")
    assert "\n\n" in result or result.count("\n") == 2
    assert not any(line and not line.strip() for line in result.splitlines())


def test_no_such_index_leaves_the_proof_untouched():
    proof = "have h : True := by sorry"
    assert fill_hole(proof, 5, "exact trivial") == proof


# --------------------------------------------------------------- bare_tactic
def test_a_leading_by_is_stripped():
    """MEASURED: `cheap_attempt()` returns a whole standalone proof body --
    `by` included -- because that is what ITS OTHER callers need. Used
    directly as a hole-filler this produced `:= by by ...`, not valid Lean
    (`by` is a term, not a tactic)."""
    assert bare_tactic("by\n  first\n    | omega") == "first\n    | omega"


def test_text_with_no_leading_by_is_unchanged():
    """The ordinary case: `HOLE_PROMPT` explicitly asks for "what follows
    `by`", so a well-behaved reply never has one to strip."""
    assert bare_tactic("exact trivial_lemma") == "exact trivial_lemma"


def test_only_the_leading_by_is_stripped_not_one_that_appears_later():
    assert bare_tactic("by simp; exact (by omega)") == "simp; exact (by omega)"


# ------------------------------------------------------------- hole_claims
def test_the_claim_is_read_from_the_enclosing_have():
    proof = "have h : 0 < n := by sorry\nexact h"
    assert hole_claims(proof) == ["0 < n"]


def test_a_hole_with_no_enclosing_have_has_no_claim():
    """The final tactic of a skeleton, not itself a subgoal."""
    assert hole_claims("sorry") == [""]
