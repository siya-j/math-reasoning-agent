"""The PutnamBench adapter. Offline — no network, no model, no Lean.

`test_the_existing_twenty_five_goal_benchmark_is_untouched` is the one that
guards the thing we care about most: PutnamBench must not disturb the only
set of numbers we currently trust — same role ProofNet's own equivalent test
plays.
"""

import json

from eval import putnam
from eval.proof_dataset import Goal, Tier, load_goals

# A hand-written fixture, not a real clone — same discipline as ProofNet's
# ROW constant. Shaped like a real PutnamBench file: import, an `open`, a
# doc comment, then the theorem with a `sorry` placeholder.
FIXTURE = """import Mathlib

open scoped BigOperators

/-- Prove that for all positive integers $n$, the sum telescopes. -/
theorem putnam_1985_a1
    (n : ℕ) (hn : 0 < n) :
    True := by sorry
"""

TERM_MODE_FIXTURE = """import Mathlib

theorem putnam_1970_a1 : True := sorry
"""

NO_SORRY_FIXTURE = """import Mathlib

theorem putnam_1970_a2 : True := by trivial
"""

# PutnamBench's own convention for a problem needing a numeric answer filled
# in first — an `_solution` abbreviation the theorem's statement references.
ANSWER_HOLE_FIXTURE = """import Mathlib

abbrev putnam_1988_a1_solution : ℕ := sorry

/-- What is the answer? -/
theorem putnam_1988_a1 : (5 : ℕ) = putnam_1988_a1_solution := by sorry
"""

INFORMAL_JSON = {
    "putnam_1985_a1": {
        "problem_name": "putnam_1985_a1",
        "informal_statement": "The canonical MAA statement of the problem.",
    }
}


# ------------------------------------------------------------- the header
def test_the_header_is_split_from_the_statement():
    parsed = putnam.parse_file(FIXTURE)

    assert "import Mathlib" in parsed["header"]
    assert "open scoped BigOperators" in parsed["header"]
    assert "theorem putnam_1985_a1" not in parsed["header"]


def test_the_open_declarations_are_kept_and_import_is_dropped():
    parsed = putnam.parse_file(FIXTURE)
    kept = putnam.opens(parsed["header"])

    assert "open scoped BigOperators" in kept
    assert "import Mathlib" not in kept


def test_the_header_survives_build_source_and_only_the_goal_is_renamed():
    """The whole reason this needs no change to math_v2 — same test ProofNet
    already runs, over PutnamBench's own file shape."""
    from verifiers.lean_verifier import build_source

    parsed = putnam.parse_file(FIXTURE)
    source = build_source(putnam.statement_with_header(parsed), "by sorry")

    assert "import Mathlib" in source
    assert "open scoped BigOperators" in source
    assert "theorem mra_goal" in source
    assert "putnam_1985_a1" not in source, "the goal name was not renamed"


# ------------------------------------------------------------ the sorry suffix
def test_the_sorry_suffix_is_stripped():
    parsed = putnam.parse_file(FIXTURE)

    assert parsed["statement"].rstrip().endswith("True")
    assert "sorry" not in parsed["statement"]
    assert ":=" not in parsed["statement"]


def test_a_term_mode_bare_sorry_is_also_stripped():
    parsed = putnam.parse_file(TERM_MODE_FIXTURE)

    assert parsed is not None
    assert "sorry" not in parsed["statement"]


def test_a_proof_body_that_is_not_a_placeholder_is_skipped():
    """A file with no `sorry` at all is not a goal to prove — it looks
    already-proved or otherwise not in the expected benchmark shape."""
    assert putnam.parse_file(NO_SORRY_FIXTURE) is None


# ------------------------------------------------------- the answer-hole subset
def test_a_problem_needing_a_filled_answer_is_skipped_and_reported():
    assert putnam.parse_file(ANSWER_HOLE_FIXTURE) is None


# --------------------------------------------------------------- informal text
def test_the_doc_comment_is_used_as_informal_text_when_putnam_json_is_absent():
    parsed = putnam.parse_file(FIXTURE)
    goal = putnam.to_goal(parsed, informal={})

    assert "telescopes" in goal["goal"]


def test_informal_putnam_json_text_is_preferred_when_present():
    parsed = putnam.parse_file(FIXTURE)
    goal = putnam.to_goal(parsed, informal=INFORMAL_JSON)

    assert "canonical MAA statement" in goal["goal"]


def test_neither_source_present_falls_back_to_none_given():
    parsed = putnam.parse_file(TERM_MODE_FIXTURE)   # no doc comment
    goal = putnam.to_goal(parsed, informal={})

    assert "(none given)" in goal["goal"]


def test_load_informal_degrades_quietly_when_the_file_is_absent(tmp_path):
    assert putnam.load_informal(tmp_path / "does-not-exist.json") == {}


def test_load_informal_keys_entries_by_problem_name(tmp_path):
    path = tmp_path / "putnam.json"
    path.write_text(json.dumps([INFORMAL_JSON["putnam_1985_a1"]]), encoding="utf-8")

    loaded = putnam.load_informal(path)

    assert loaded["putnam_1985_a1"]["informal_statement"] == (
        "The canonical MAA statement of the problem."
    )


# --------------------------------------------------------------- the shape
def test_a_converted_problem_loads_as_a_Goal(tmp_path):
    parsed = putnam.parse_file(FIXTURE)
    path = tmp_path / "putnam.json"
    path.write_text(json.dumps([putnam.to_goal(parsed, {})]), encoding="utf-8")

    goals = load_goals(path)

    assert len(goals) == 1
    assert isinstance(goals[0], Goal)
    assert goals[0].id == "putnam_1985_a1"
    assert goals[0].tier is Tier.PUTNAM


def test_the_tier_exists_so_metrics_and_selection_work():
    assert Tier.PUTNAM.value == "putnam"


def test_the_area_label_groups_by_contest_year():
    assert putnam.area_of("putnam_1985_a1") == "putnam 1985"
    assert putnam.area_of("putnam_2001_b3") == "putnam 2001"
    assert putnam.area_of("odd_name") == "putnam"


# ------------------------------------------------- do not disturb the twenty-five
def test_the_existing_twenty_five_goal_benchmark_is_untouched():
    """THE guard. PutnamBench must not perturb the numbers we already trust."""
    goals = load_goals()          # the default eval/proofs.json

    assert len(goals) == 25
    assert len([g for g in goals if g.tier is Tier.NEAR_MATHLIB]) == 7
    assert not any(g.tier is Tier.PUTNAM for g in goals), (
        "PutnamBench leaked into the curated dataset"
    )


# `test_the_evaluator_can_be_pointed_at_another_goals_file` in
# tests/test_proofnet_adapter.py already asserts general `--goals` wiring in
# scripts/evaluate_proofs.py — nothing PutnamBench-specific about that
# assertion, so it is not duplicated here.


# ------------------------------------------------------------ known caveats
def test_the_licensing_note_is_stated_where_it_will_be_read():
    """informal/putnam.json needs MAA permission. That cannot be a footnote."""
    text = __import__("pathlib").Path("eval/putnam.py").read_text(encoding="utf-8")

    assert "MAA" in text
    assert "informal/putnam.json" in text


def test_generated_goal_files_are_gitignored():
    text = __import__("pathlib").Path(".gitignore").read_text(encoding="utf-8")

    assert "eval/putnam" in text
