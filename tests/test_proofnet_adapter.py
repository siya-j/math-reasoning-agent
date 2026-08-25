"""The ProofNet adapter. Offline — no network, no model, no Lean.

`test_the_existing_seven_goal_benchmark_is_untouched` is the one that guards
the thing we care about most: ProofNet must not disturb the only set of
numbers we currently trust.
"""

import json

import pytest

from eval import proofnet
from eval.proof_dataset import Goal, Tier, load_goals
from verifiers.lean_verifier import build_source

# A real row, quoted verbatim from proofnet/valid.jsonl.
ROW = {
    "name": "exercise_1_13a",
    "split": "valid",
    "informal_prefix": (
        "/-- Suppose that $f$ is holomorphic in an open set $\\Omega$. "
        "Prove that if $\\text{Re}(f)$ is constant, then $f$ is constant.-/\n"
    ),
    "formal_statement": (
        "theorem exercise_1_13a {f : ℂ → ℂ} (Ω : Set ℂ) (a b : Ω) "
        "(h : IsOpen Ω)\n  (hf : DifferentiableOn ℂ f Ω) :\n  f a = f b :="
    ),
    "goal": "f : ℂ → ℂ\n⊢ f ↑a = f ↑b",
    "header": (
        "import Mathlib\n\nopen Complex Filter Function Metric Finset\n"
        "open scoped BigOperators Topology\n\n"
    ),
}


# ------------------------------------------------------------- the header
def test_the_open_declarations_are_kept_and_the_import_is_dropped():
    """Without the opens, `abs`, `∑` and `𝓝` do not elaborate.

    The preamble already imports Mathlib, so a second import is noise.
    """
    kept = proofnet.opens(ROW["header"])

    assert "open Complex Filter Function Metric Finset" in kept
    assert "open scoped BigOperators Topology" in kept
    assert "import Mathlib" not in kept


def test_the_header_survives_build_source_and_only_the_goal_is_renamed():
    """The whole reason this needs no change to math_v2.

    `rename_goal` renames the LAST declaration; `open` lines are not
    declarations, so they pass through untouched.
    """
    source = build_source(proofnet.statement_with_header(ROW), "by sorry")

    assert "import Mathlib" in source
    assert "open Complex Filter Function Metric Finset" in source
    assert "theorem mra_goal" in source
    assert "exercise_1_13a" not in source, "the goal name was not renamed"


def test_a_kept_auxiliary_lemma_still_works_alongside_the_header():
    """Decomposition must survive the ProofNet statement shape."""
    combined = (
        proofnet.opens(ROW["header"])
        + "\n\nlemma helper : True := trivial\n\n"
        + ROW["formal_statement"]
    )
    source = build_source(combined, "by sorry")

    assert "lemma helper" in source, "a kept lemma was renamed away"
    assert "theorem mra_goal" in source


def test_a_row_with_no_header_is_handled():
    assert proofnet.opens("") == ""
    assert proofnet.statement_with_header({"formal_statement": "theorem t : True :="})


# -------------------------------------------------------------- the modes
def test_formal_mode_gives_the_agent_the_lean_statement():
    """Measures the prover, and is what published ProofNet numbers measure."""
    goal = proofnet.to_goal(ROW, mode="formal")

    assert "theorem exercise_1_13a" in goal["goal"]
    assert "open Complex" in goal["goal"]
    assert "holomorphic" in goal["goal"], "the informal claim should ride along"


def test_informal_mode_withholds_the_lean_statement():
    """Otherwise it is not measuring formalisation at all."""
    goal = proofnet.to_goal(ROW, mode="informal")

    assert "holomorphic" in goal["goal"]
    assert "theorem" not in goal["goal"]
    assert "DifferentiableOn" not in goal["goal"]


def test_the_formal_statement_is_kept_as_metadata_in_both_modes():
    """Needed to inspect a failure, and for a future preservation check."""
    for mode in ("formal", "informal"):
        assert "DifferentiableOn" in proofnet.to_goal(ROW, mode)["note"]


def test_the_doc_comment_wrapper_is_stripped():
    text = proofnet.strip_comment(ROW["informal_prefix"])

    assert text.startswith("Suppose that")
    assert "/--" not in text and "-/" not in text


def test_stripping_survives_odd_input():
    assert proofnet.strip_comment("") == ""
    assert proofnet.strip_comment("plain text") == "plain text"


# --------------------------------------------------------------- the shape
def test_a_converted_row_loads_as_a_Goal(tmp_path):
    path = tmp_path / "proofnet.json"
    path.write_text(json.dumps([proofnet.to_goal(ROW, "formal")]), encoding="utf-8")

    goals = load_goals(path)

    assert len(goals) == 1
    assert isinstance(goals[0], Goal)
    assert goals[0].id == "exercise_1_13a"
    assert goals[0].tier is Tier.PROOFNET


def test_the_tier_exists_so_metrics_and_selection_work():
    assert Tier.PROOFNET.value == "proofnet"


def test_the_area_label_groups_by_source_chapter():
    assert proofnet.area_of("exercise_1_13a") == "proofnet 1"
    assert proofnet.area_of("exercise_2_3_1") == "proofnet 2"
    assert proofnet.area_of("odd_name") == "proofnet"


# ------------------------------------------------- do not disturb the seven
def test_the_existing_seven_goal_benchmark_is_untouched():
    """THE guard. ProofNet must not perturb the numbers we already trust."""
    goals = load_goals()          # the default eval/proofs.json

    assert len(goals) == 20
    assert len([g for g in goals if g.tier is Tier.NEAR_MATHLIB]) == 7
    assert not any(g.tier is Tier.PROOFNET for g in goals), (
        "ProofNet leaked into the curated dataset"
    )


def test_the_evaluator_can_be_pointed_at_another_goals_file():
    source = (
        __import__("pathlib").Path("scripts/evaluate_proofs.py")
        .read_text(encoding="utf-8")
    )
    assert '"--goals"' in source
    assert "load_goals(Path(args.goals) if args.goals else None)" in source


# ------------------------------------------------------------ known caveats
def test_the_contamination_warning_is_stated_where_it_will_be_read():
    """31.8% of the port is reported mis-formalised. That cannot be a footnote."""
    text = __import__("pathlib").Path("eval/proofnet.py").read_text(encoding="utf-8")

    assert "31.8%" in text
    assert "2406.07222" in text


def test_both_dataset_variants_are_reachable():
    assert proofnet.DATASETS["v3"] == "UDACA/proofnet-v3-lean4"
    assert proofnet.DATASETS["v1"] == "UDACA/proofnet-lean4"
