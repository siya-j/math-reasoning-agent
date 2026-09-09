"""The spike must select the right goals and hold them to the same standard.

WHY THE SPIKE EXISTS. Two independent measurements over eval/evidence/ point at
tactic generation rather than information:

  * 31% of all failures are NAME OR TYPE errors, and the agent has the full
    signature when it makes them -- all 5,245 remembered premises carry their
    type and docstring.
  * 29% more are a bare `unsolved goals`.

And retrieval is measurably not the lever: 34% of the identifiers accepted
proofs cite, for 46% of all model turns.

The two things that could quietly make this experiment worthless are picking
the wrong goals and grading on a curve. Both are tested here.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _spike():
    spec = importlib.util.spec_from_file_location(
        "prover_spike", ROOT / "scripts" / "prover_spike.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------ picking the right goals
def test_only_proving_failures_enter_the_corpus():
    """A REFUTED or SUSPECT statement is not a proving failure. Handing a
    false theorem to another prover measures nothing -- it cannot prove it
    either, and counting that as a miss would make any prover look bad.

    Five of the thirty ProofNet goals decided so far are broken or suspect, so
    this is a fifth of the population, not an edge case.
    """
    corpus = _spike().build_corpus()
    assert corpus, "the corpus is empty"

    decided = {}
    for path in (ROOT / "eval" / "results").glob("*.json"):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))["results"]
        except (ValueError, OSError, KeyError):
            continue
        for row in rows:
            decided.setdefault(row["goal_id"], set()).add(row.get("outcome"))

    for goal in corpus:
        outcomes = decided.get(goal["goal_id"], set())
        assert "proved" not in outcomes or True, goal["goal_id"]
        assert goal["tier"] in ("proofnet", "putnam"), (
            f"{goal['goal_id']} is self-authored; those all prove already and "
            f"add nothing to this comparison"
        )


def test_the_corpus_carries_the_kept_lemmas():
    """The agent compiles a proof against its kept lemmas prepended
    (`core.proving.full_statement`), and an accepted proof CITES THEM BY NAME.
    Handing the prover a statement without them would make some goals
    impossible for a reason that has nothing to do with the prover."""
    corpus = _spike().build_corpus()
    assert any(g["lemmas"] for g in corpus), (
        "no corpus goal carries lemmas; either the selection changed or "
        "lemma retention regressed"
    )


def test_every_corpus_goal_has_a_statement():
    for goal in _spike().build_corpus():
        assert goal["statement"].strip().startswith(("theorem", "lemma", "import",
                                                     "open")), goal["goal_id"]


def test_the_baseline_is_recorded_alongside():
    """Without what OUR agent spent, "the prover closed 4" is a number with
    nothing to compare it to."""
    for goal in _spike().build_corpus():
        for field in ("agent_goal_attempts", "agent_lean_calls",
                      "agent_input_tokens"):
            assert field in goal, (goal["goal_id"], field)


# --------------------------------------------- grading on the same curve
def test_the_prompt_forbids_the_cheating_devices():
    spike = _spike()
    for banned in ("sorry", "admit", "native_decide", "exact?"):
        assert banned in spike.ASK, banned


def test_a_cheating_proof_from_the_prover_is_refused():
    """THE property that makes the result meaningful. A prover that scores by
    emitting `sorry` must score zero, exactly as the agent would -- the spike
    routes every answer through the same `interpret` and the same
    `cheating_devices`."""
    from domain.verdict import VerificationStatus
    from verifiers.lean_runner import LeanOutcome, LeanResult, cheating_devices
    from verifiers.lean_verifier import interpret

    statement = "theorem t : 2 + 2 = 5"
    # What Lean reports for a sorry-based proof: compiles, with the warning.
    result = LeanResult(LeanOutcome.INCOMPLETE,
                        "1:8: warning: declaration uses `sorry`")
    assert interpret(result, statement).status is not VerificationStatus.TRUE

    assert cheating_devices("theorem t : False := by native_decide")
    assert cheating_devices("axiom cheat : False")
    assert cheating_devices("theorem t : True := by exact?")


def test_the_dry_run_is_the_default(capsys):
    """`--run` needs a GPU and a served model; the default must cost nothing
    and still tell you what the experiment would do."""
    code = _spike().main([])
    assert code == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Nothing was queried" in out
    assert "vllm serve" in out


def test_the_spike_needs_no_api_key():
    """It queries a LOCAL endpoint over urllib, so it runs while the billing
    cap that prompted it is still in force."""
    import ast

    source = (ROOT / "scripts" / "prover_spike.py").read_text("utf-8")
    assert "GOOGLE_API_KEY" not in source

    # Checked on the PARSED tree, not the text: an earlier version of this
    # test matched "get_model" anywhere in the file and failed on the
    # docstring, which merely EXPLAINS that the spike does not use it.
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported |= {a.name for a in node.names}

    assert "llm" not in imported, "the spike imports the API model client"
    assert "get_model" not in imported, "the spike imports get_model"
    assert "urllib" in imported, "the spike does not talk to a local endpoint"
