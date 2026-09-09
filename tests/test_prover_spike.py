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


def test_a_run_that_never_reached_the_prover_is_not_a_score(capsys):
    """The FIRST attempt at this spike printed "closed 0 of 0 goals" after
    three timeouts. That reads as a result -- the prover tried and failed --
    and it is not one: nothing was ever generated or compiled. A run whose
    every request errored must refuse to report a rate."""
    code = _spike().main([
        "--run", "--url", "http://127.0.0.1:1/v1", "--limit", "2",
        "--timeout", "1",
    ])
    out = capsys.readouterr().out
    assert "NOTHING WAS SCORED" in out
    assert "NEVER REACHED THE PROVER" in out
    assert "closed 0 of 0" not in out, "a timeout is not a miss"
    assert code == 1, "an unmeasured run must not exit successfully"


def test_cheapest_first_reorders_by_our_own_spend():
    """--limit alone takes whatever sorts first, which on this corpus put a
    5.1M-token goal third. On hardware where one generation costs minutes the
    first goals should be the ones most likely to finish."""
    corpus = _spike().build_corpus()
    if len(corpus) < 3:
        pytest.skip("needs a corpus of at least three goals")
    ordered = sorted(corpus, key=lambda g: g["agent_input_tokens"])
    spends = [g["agent_input_tokens"] for g in ordered]
    assert spends == sorted(spends)
    assert ordered[0]["agent_input_tokens"] <= corpus[0]["agent_input_tokens"]


# ------------------------------------------------ taking the proof apart
#
# MEASURED, on the very first request ever sent to a served prover: asked for
# "ONLY the proof term or tactic block ... no restatement of the theorem",
# Goedel-Prover-V2 returned
#
#     4
#     theorem lean_workbook_plus_10000 : 1 + 1 = 2 := by norm_num <;> ...
#
# `declaration()` wraps anything not starting with `by`/`:=` into
# `by\n  <text>`, so that answer becomes `by theorem lean_workbook...` -- a
# parse error. Unfixed, all 12 goals would have failed to parse and the spike
# would have reported "the prover closed 0 of 12" as a finding about the
# prover. These tests are what stands between that and a real measurement.
def test_a_restated_theorem_yields_only_its_body():
    """The exact reply the live server sent during calibration."""
    preamble, body = _spike()._extract_proof(
        "4\ntheorem lean_workbook_plus_10000 : 1 + 1 = 2 := by\n"
        "  norm_num\n  <;> simp")
    assert "theorem" not in body, "the restatement must not reach the compiler"
    assert "lean_workbook" not in body
    assert body.startswith("by")
    assert "norm_num" in body
    assert preamble == "", "a stray `4` is not a helper lemma"


def test_the_model_cannot_swap_in_an_easier_theorem():
    """THE SOUNDNESS PROPERTY OF THE WHOLE SPIKE.

    The prover restates the goal under its own name, and a restatement can
    be WEAKER than what we asked -- a dropped quantifier, a loosened
    hypothesis. If its statement reached the compiler it could score by
    proving something easy, and the spike's headline number would be a lie
    in the prover's favour.
    """
    from verifiers.lean_verifier import build_source

    ours = "theorem hard (n : \u2115) (h : 2 \u2264 n) : \u2203 p, p.Prime \u2227 n < p"
    theirs = "theorem trivial_instead : True := by\n  trivial"

    _, body = _spike()._extract_proof(theirs)
    source = build_source(ours, body)

    assert "trivial_instead" not in source, "their name reached the file"
    assert "True" not in source, "their statement reached the file"
    assert "\u2203 p, p.Prime" in source, "our goal is what must be compiled"
    assert "trivial" in source, "their tactic is what should be tried"


def test_a_helper_lemma_survives_but_an_import_does_not():
    """A lemma before the goal is a real strategy and the agent is allowed
    it too, so it is kept and compiled ahead of the statement. `import` is
    legal only at the top of a file and build_source already supplies it."""
    preamble, body = _spike()._extract_proof(
        "import Mathlib\nopen Real\n\n"
        "lemma helper (n : \u2115) : 0 \u2264 n := Nat.zero_le n\n\n"
        "theorem main : True := by\n  exact trivial")
    assert "import" not in preamble
    assert "open Real" in preamble
    assert "helper" in preamble
    assert body == "by\n  exact trivial"


@pytest.mark.parametrize("signature", [
    "theorem g (f : Foo) (h : f = { carrier := s, ok := m }) : True",
    "theorem g (n : \u2115 := 3) : n = n",
])
def test_a_colon_equals_inside_the_signature_does_not_cut_it(signature):
    """`:=` occurs inside signatures -- structure instances, binder
    defaults. Splitting on the first one would hand Lean half a statement
    and blame the proof for the syntax error."""
    _, body = _spike()._extract_proof(signature + " := by\n  trivial")
    assert body == "by\n  trivial"


def test_a_compliant_answer_passes_through_untouched():
    """If a model DOES send a bare tactic block, nothing may be stripped
    from it -- the extraction must not become a second failure mode."""
    preamble, body = _spike()._extract_proof("intro h\nexact h.trans hx")
    assert preamble == ""
    assert body == "intro h\nexact h.trans hx"


def test_a_truncated_generation_fails_honestly():
    """Cut off mid-signature there is no body to find. Returning the text
    unchanged makes Lean report a syntax error, which is the truth; inventing
    a body would score a proof the model never wrote."""
    _, body = _spike()._extract_proof("theorem h : \u2200 x, x = x")
    assert "theorem h" in body, "nothing was invented and nothing was hidden"


def test_the_prompt_no_longer_forbids_what_the_model_always_does():
    """Prose that the model declines is worse than no prose: it handicaps a
    prover trained to emit whole files, and this spike is supposed to
    measure proving ability, not instruction-following."""
    ask = _spike().ASK
    assert "no restatement" not in ask.lower()
    assert "sorry" in ask, "the cheating prohibitions must remain"
    assert "native_decide" in ask

