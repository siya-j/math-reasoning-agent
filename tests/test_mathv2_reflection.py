"""The three pre-benchmark improvements: proof_state, no-repeat, and workflow.

All three exist to make reflection, decomposition and synthesis observable
rather than hoped for. `test_an_identical_proof_is_refused_without_compiling`
is the load-bearing one: it guards the exact measured failure that motivated
the agentic architecture in the first place.
"""

import asyncio

import pytest

from math_v2.context import MathContext
from math_v2.core import log, progress, proving as core_proving
from math_v2.tools import proving as proving_tools
from math_v2.tools.proving import proof_state, try_proof

STATEMENT = "theorem mra_goal : 2 + 2 = 4"


def run(coro):
    return asyncio.run(coro)


def runtime_for(workdir):
    from langchain.tools import ToolRuntime

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


@pytest.fixture
def lean_calls(monkeypatch):
    calls = []

    async def fake(source):
        calls.append(source)
        from verifiers.lean_runner import LeanOutcome, LeanResult

        return LeanResult(LeanOutcome.ERRORS, "f.lean:4:2: error: unsolved goals\n⊢ P")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda w: fake)
    return calls


# ------------------------------------------------------------- no repeats
def test_an_identical_proof_is_refused_without_compiling(tmp_path, lean_calls):
    """THE guard. The baseline emitted byte-identical proposals; this stops it.

    Twenty seconds spent re-learning a known answer is twenty seconds not spent
    on a new idea.
    """
    rt = runtime_for(tmp_path)
    first = run(try_proof.ainvoke({"proof": "by simp", "statement": STATEMENT,
                                   "runtime": rt}))
    second = run(try_proof.ainvoke({"proof": "by simp", "runtime": rt}))

    assert first["outputs"]["accepted"] is False
    assert second["error"] == "duplicate_attempt"
    assert len(lean_calls) == 1, "the duplicate reached the compiler"


def test_the_refusal_repeats_what_lean_said(tmp_path, lean_calls):
    """Otherwise the agent has to remember why, which is what failed before."""
    rt = runtime_for(tmp_path)
    run(try_proof.ainvoke({"proof": "by simp", "statement": STATEMENT, "runtime": rt}))
    second = run(try_proof.ainvoke({"proof": "by simp", "runtime": rt}))

    assert "unsolved goals" in second["message"]
    assert "proof_state" in second["message"], "no route out of the loop was offered"


def test_whitespace_is_not_a_change(tmp_path, lean_calls):
    rt = runtime_for(tmp_path)
    run(try_proof.ainvoke({"proof": "by simp", "statement": STATEMENT, "runtime": rt}))
    again = run(try_proof.ainvoke({"proof": "by   simp\n", "runtime": rt}))

    assert again["error"] == "duplicate_attempt"


def test_a_genuinely_different_proof_is_compiled(tmp_path, lean_calls):
    """The check must stop repetition, not stop work.

    `by norm_num` after `by simp` is no longer "different" — both are closers
    `try_standard_tactics` runs in one file, so the second cannot learn
    anything the first did not. A real argument still compiles.
    """
    rt = runtime_for(tmp_path)
    run(try_proof.ainvoke({"proof": "by simp", "statement": STATEMENT, "runtime": rt}))
    run(try_proof.ainvoke({"proof": "by\n  have h : 2 + 2 = 4 := by norm_num\n  exact h",
                           "runtime": rt}))

    assert len(lean_calls) == 2


def test_a_repeat_against_a_DIFFERENT_statement_is_allowed(tmp_path, lean_calls):
    """The same tactic may be right for one goal and wrong for another."""
    rt = runtime_for(tmp_path)
    run(try_proof.ainvoke({"proof": "by simp", "statement": STATEMENT, "runtime": rt}))
    run(try_proof.ainvoke({"proof": "by simp", "statement": "theorem other : 3 + 3 = 6",
                           "runtime": rt}))

    assert len(lean_calls) == 2


def test_an_ACCEPTED_proof_can_be_resubmitted(tmp_path, monkeypatch):
    """Only rejected attempts are duplicates; a success is not a mistake."""
    from verifiers.lean_runner import LeanOutcome, LeanResult

    async def ok(source):
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda w: ok)
    rt = runtime_for(tmp_path)

    run(try_proof.ainvoke({"proof": "by norm_num", "statement": STATEMENT,
                           "runtime": rt}))
    again = run(try_proof.ainvoke({"proof": "by norm_num", "runtime": rt}))
    assert again.get("error") != "duplicate_attempt"


def test_the_duplicate_check_is_code_and_not_a_prompt_rule():
    """A rule only in the prompt is a rule the model can decline."""
    assert callable(core_proving.already_tried)


# ------------------------------------------------------------- proof_state
def test_proof_state_costs_no_compilation(tmp_path, lean_calls):
    result = run(proof_state.ainvoke({"runtime": runtime_for(tmp_path)}))
    assert result["ok"] is True
    assert lean_calls == []


def test_proof_state_reports_lemmas_by_name_so_they_can_be_cited(tmp_path):
    log.set_goal(str(tmp_path), STATEMENT)
    log.keep_lemma(str(tmp_path), "lemma helper (n : Nat) : n + 0 = n := by simp")

    result = run(proof_state.ainvoke({"runtime": runtime_for(tmp_path)}))

    assert "helper" in result["message"]
    assert result["outputs"]["lemmas_proved"]


def test_proof_state_reports_failures_with_their_errors(tmp_path):
    log.set_goal(str(tmp_path), STATEMENT)
    log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=STATEMENT,
                                         proof="by simp", status=log.UNKNOWN,
                                         detail="error: unsolved goals"))

    result = run(proof_state.ainvoke({"runtime": runtime_for(tmp_path)}))

    assert "by simp" in result["message"]
    assert "unsolved goals" in result["message"]
    assert "Do not resubmit" in result["message"]


def test_proof_state_reports_open_holes_from_a_typechecking_skeleton(tmp_path):
    log.set_goal(str(tmp_path), STATEMENT)
    log.append(str(tmp_path), log.Record(
        kind=log.SKELETON, statement=STATEMENT, status=log.TRUE,
        proof="have h1 : 1 = 1 := by sorry\nhave h2 : 2 = 2 := by sorry\nexact h1",
    ))

    result = run(proof_state.ainvoke({"runtime": runtime_for(tmp_path)}))

    assert result["outputs"]["open_holes"] == ["1 = 1", "2 = 2"]
    assert "try_lemma" in result["message"]


def test_holes_from_a_skeleton_that_did_not_typecheck_are_not_offered(tmp_path):
    """Its steps do not combine into the goal, so they are not subgoals of it."""
    log.append(str(tmp_path), log.Record(
        kind=log.SKELETON, statement=STATEMENT, status=log.FALSE,
        proof="have h : 1 = 1 := by sorry",
    ))
    assert progress.open_holes(str(tmp_path)) == []


def test_proof_state_reports_symbolic_results_as_evidence(tmp_path):
    log.append(str(tmp_path), log.Record(
        kind="computation", statement="check_primality(lhs='561')",
        status=log.FALSE, detail="561 = 3 x 11 x 17",
    ))

    result = run(proof_state.ainvoke({"runtime": runtime_for(tmp_path)}))

    assert "check_primality" in result["message"]
    assert "evidence, not proof" in result["message"]


def test_proof_state_never_claims_a_proof_the_record_lacks(tmp_path):
    log.set_goal(str(tmp_path), STATEMENT)
    log.append(str(tmp_path), log.Record(kind=log.LEMMA, statement="lemma h : True",
                                         status=log.TRUE, proof="trivial"))
    log.keep_lemma(str(tmp_path), "lemma h : True := trivial")

    state = progress.snapshot(str(tmp_path))
    assert state["proved"] is False, "a helper lemma was read as the goal"


def test_proof_state_points_at_decomposition_after_repeated_failure(tmp_path):
    log.set_goal(str(tmp_path), STATEMENT)
    for proof in ("one", "two"):
        log.append(str(tmp_path), log.Record(kind=log.PROOF, statement=STATEMENT,
                                             proof=proof, status=log.UNKNOWN,
                                             detail="error"))

    message = progress.render(progress.snapshot(str(tmp_path)))
    assert "try_skeleton" in message


def test_proof_state_on_an_empty_record_says_so_rather_than_inventing(tmp_path):
    message = progress.render(progress.snapshot(str(tmp_path)))
    assert "No formal statement is set yet" in message
    assert "No auxiliary lemmas proved yet" in message


def test_proof_state_is_read_only(tmp_path):
    """It reflects execution; it must not become a way to change the record."""
    log.set_goal(str(tmp_path), STATEMENT)
    before = log.read(str(tmp_path))

    run(proof_state.ainvoke({"runtime": runtime_for(tmp_path)}))

    after = log.read(str(tmp_path))
    assert after["records"] == before["records"]
    assert after["lemmas"] == before["lemmas"]


# ------------------------------------------------------- the SymPy workflow
def test_the_prompt_directs_symbolic_work_before_formalising():
    """The bridge that makes this a reasoning system rather than a prover."""
    from math_v2.prompt import MATH_SYSTEM_PROMPT as prompt

    for instruction in ("TEST SMALL CASES", "FIND A WITNESS",
                        "LOOK FOR A COUNTEREXAMPLE", "FACTOR, EXPAND, SIMPLIFY"):
        assert instruction in prompt, instruction


def test_the_prompt_connects_computations_to_auxiliary_lemmas():
    from math_v2.prompt import MATH_SYSTEM_PROMPT as prompt

    assert "candidate auxiliary lemma" in prompt
    assert "try_lemma" in prompt and "try_skeleton" in prompt


def test_the_prompt_still_forbids_a_computation_standing_in_for_a_proof():
    """The workflow must not blur the line the guard enforces."""
    from math_v2.prompt import MATH_SYSTEM_PROMPT as prompt

    assert "EVIDENCE, never a proof" in prompt
    assert "A computation is not a proof" in prompt


def test_no_new_symbolic_operations_were_added():
    from math_v2.core import symbolic

    assert len(symbolic.OPS) == 9


def test_the_lemma_and_skeleton_machinery_is_untouched():
    from math_v2.tools import create_math_v2_tools

    names = {t.name for t in create_math_v2_tools()}
    assert {"try_lemma", "try_skeleton"} <= names
    assert len(names) == 18          # 16, plus proof_state and try_refutation


# --------------------------------------------------------- no placeholders
def test_a_sorry_proof_is_refused_without_compiling(tmp_path, lean_calls):
    """Measured on proofnet `exercise_1_13a`: attempt 2 of 3 was `by sorry`.

    Lean ACCEPTS it, so the compile teaches nothing the text did not already
    say — and the budget is three attempts.
    """
    rt = runtime_for(tmp_path)
    result = run(try_proof.ainvoke({"proof": "by sorry", "statement": STATEMENT,
                                    "runtime": rt}))

    assert result["error"] == "placeholder_proof"
    assert lean_calls == [], "the placeholder reached the compiler"
    assert "try_skeleton" in result["message"], "no route out was offered"


def test_admit_is_refused_too(tmp_path, lean_calls):
    rt = runtime_for(tmp_path)
    result = run(try_proof.ainvoke({"proof": "by admit", "statement": STATEMENT,
                                    "runtime": rt}))

    assert result["error"] == "placeholder_proof"


def test_a_refused_placeholder_does_not_spend_an_attempt(tmp_path, lean_calls):
    """It is not logged, so it cannot be read back as a proof attempt."""
    rt = runtime_for(tmp_path)
    run(try_proof.ainvoke({"proof": "by sorry", "statement": STATEMENT, "runtime": rt}))

    assert log.records(str(tmp_path), log.PROOF) == []


def test_a_real_proof_still_compiles(tmp_path, lean_calls):
    """The check must stop placeholders, not stop work."""
    rt = runtime_for(tmp_path)
    run(try_proof.ainvoke({"proof": "by norm_num", "statement": STATEMENT,
                           "runtime": rt}))

    assert len(lean_calls) == 1
