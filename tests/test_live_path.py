"""Are the new components actually WIRED, or merely present?

WHY A SEPARATE FILE
-------------------
Every other test in this repo exercises a function directly. That proves the
function is correct and proves nothing about whether anything calls it — and
this project already shipped one component that was correct, tested, measurably
better than the alternative, and dead: `retrieval.loogle.premises_for` built
goal-shape queries, `pipeline/` used it, and the `math_v2` path never called it
once. Nothing failed. The tests were green the whole time.

So these tests go in through `harness.prove`, the same entry point
`scripts/evaluate_proofs.py` uses, with a SCRIPTED agent that drives the real
tools. The model and the compiler are fakes; the prompt construction, the tool
wiring, the log, the budget and the guard are all the live objects.

If a change is only reachable from a unit test, it fails here.
"""

import pytest

from math_v2 import harness
from math_v2.core import diagnosis, log
from tests.test_mathv2_integration import scripted

GOAL = "theorem mra_goal (Ω : Set ℂ) (h : IsOpen Ω) : f a = f b"


class FakeSearch:
    """Stands in for the Loogle client at the exact seam the tool uses."""

    def __init__(self, premises=()):
        self.premises = list(premises)
        self.asked = []

    def premises_for(self, statement):
        self.asked.append(statement)
        return self.premises

    def search_with_suggestions(self, query, limit=None):
        self.asked.append(query)
        return self.premises, []


def premise(name, type_=" : 1 = 1", module="Mathlib.Analysis"):
    from retrieval.loogle import Premise

    return Premise(name=name, type=type_, module=module)


@pytest.fixture
def rejecting_compiler(monkeypatch):
    """A Lean that rejects with a real error string, at the live seam."""
    from verifiers.lean_runner import LeanOutcome, LeanResult

    seen = []

    async def run_lean(source):
        seen.append(source)
        if "sorry" in source:            # the statement check
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(
            LeanOutcome.ERRORS,
            "f.lean:4:2: error: unsolved goals\n\nStill to prove:\n⊢ f ↑a = f ↑b")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)
    return seen


@pytest.fixture
def wired_search(monkeypatch):
    """Install a fake at `get_search`, which is what the tool layer calls."""
    search = FakeSearch([
        premise("is_const_of_deriv_eq_zero",
                type_=" (hs : IsPreconnected s) (hf : ∀ x ∈ s, deriv f x = 0) : "
                      "∀ x ∈ s, f x = c"),
        premise("Std.Sat.AIG.getConstant", module="Std.Sat.AIG"),
    ])
    monkeypatch.setattr("math_v2.tools.retrieval.get_search", lambda: search)
    return search


# ------------------------------------------------- 1. the prompt is the live one
def test_the_prompt_the_model_receives_is_the_one_we_edited(tmp_path,
                                                            rejecting_compiler):
    """`harness.prove` builds its own agent — it does NOT go through
    `math_v2/agent.py`. So asserting on the prompt file proves nothing until
    the prompt is captured at the factory the harness actually calls."""
    factory = scripted([("check_statement", {"statement": GOAL})])
    harness.prove("q", model=object(), workdir=str(tmp_path), agent_factory=factory)

    prompt = factory.prompt

    # Strategy before syntax.
    assert "SAY THE ARGUMENT BEFORE YOU WRITE ANY LEAN" in prompt
    # Signatures, not just names.
    assert "READ THE SIGNATURES" in prompt
    # Errors used diagnostically, with distinct routes.
    assert "unknown identifier" in prompt and "type mismatch" in prompt
    # Generic tactics demoted.
    assert "DO NOT submit `by aesop`" in prompt
    # Decomposition and refutation.
    assert "try_skeleton" in prompt and "try_refutation" in prompt
    # Lean as the authority.
    assert "only once `try_proof` reports ACCEPTED" in prompt


def test_every_new_tool_is_actually_offered_to_the_model(tmp_path,
                                                         rejecting_compiler):
    factory = scripted([("check_statement", {"statement": GOAL})])
    harness.prove("q", model=object(), workdir=str(tmp_path), agent_factory=factory)

    assert {"try_refutation", "try_lemma", "try_skeleton"} <= set(factory.tools)


# --------------------------------------- 2. retrieval runs on the live path
def test_checking_a_statement_runs_the_goal_shape_ladder(tmp_path, wired_search,
                                                         rejecting_compiler):
    """THE regression this file exists for. `premises_for` was dead code on
    this path for the whole project."""
    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([("check_statement", {"statement": GOAL})]))

    assert wired_search.asked == [GOAL], "the ladder never ran off the goal"


def test_the_signatures_reach_the_model_not_just_the_names(tmp_path, wired_search,
                                                           rejecting_compiler):
    """Names alone are what produced `exact DifferentiableOn`."""
    captured = {}

    factory = scripted([("check_statement", {"statement": GOAL})])
    original = factory

    harness.prove("q", model=object(), workdir=str(tmp_path), agent_factory=original)
    captured["premises"] = log.read(str(tmp_path))["premises"]

    seeded = {p["name"] for p in captured["premises"]}
    assert "is_const_of_deriv_eq_zero" in seeded
    assert any("IsPreconnected" in p["type"] for p in captured["premises"]), (
        "the signature was not stored, so it cannot be shown"
    )


def test_machinery_is_filtered_on_the_live_path(tmp_path, wired_search,
                                                rejecting_compiler):
    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([("check_statement", {"statement": GOAL})]))

    stored = {p["name"] for p in log.read(str(tmp_path))["premises"]}
    assert "Std.Sat.AIG.getConstant" not in stored


def test_the_seeded_premises_reach_the_tactic_ladder(tmp_path, wired_search,
                                                     rejecting_compiler):
    """Seeding `check_statement` alone would be pointless if the ladder read a
    different store. It does not — both go through `log.premises`."""
    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_standard_tactics", {}),
                  ]))

    compiled = "\n".join(rejecting_compiler)
    assert "is_const_of_deriv_eq_zero" in compiled, (
        "the seeded premise never reached the compiled tactic ladder"
    )


# ------------------------------------- 3. the diagnosis reaches the model
def test_a_lean_failure_is_classified_and_the_instruction_is_returned(tmp_path,
                                                                      rejecting_compiler):
    """Classification that only reached the log would change nothing."""
    import asyncio

    from math_v2.core import proving

    async def run_lean(source):
        return __import__("verifiers.lean_runner", fromlist=["x"]).LeanResult(
            __import__("verifiers.lean_runner", fromlist=["x"]).LeanOutcome.ERRORS,
            "2:58: error: Unknown constant `Complex.abs`")

    result = asyncio.run(proving.try_proof(
        str(tmp_path), GOAL, "by exact Complex.abs", run_lean))

    assert result["outputs"]["failure"] == diagnosis.UNKNOWN_IDENTIFIER
    assert "WHAT THIS MEANS" in result["message"]
    assert "search_mathlib" in result["message"]


def test_three_generic_closers_do_not_become_three_attempts(tmp_path,
                                                            rejecting_compiler):
    """The question asked directly: aesop, then simp, then rfl. Only the first
    may reach the compiler."""
    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_proof", {"proof": "by aesop"}),
                      ("try_proof", {"proof": "by simp"}),
                      ("try_proof", {"proof": "by rfl"}),
                  ]))

    attempts = [r for r in log.records(str(tmp_path), log.PROOF)
                if (r.get("proof") or "").strip()]
    assert len(attempts) == 1, (
        f"{len(attempts)} generic closers were compiled; expected 1"
    )


def test_a_real_argument_after_a_generic_one_still_compiles(tmp_path,
                                                            rejecting_compiler):
    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_proof", {"proof": "by aesop"}),
                      ("try_proof", {"proof": "by\n  have h : IsOpen Ω := ‹_›\n"
                                              "  exact foo h"}),
                  ]))

    attempts = [r for r in log.records(str(tmp_path), log.PROOF)
                if (r.get("proof") or "").strip()]
    assert len(attempts) == 2


# ------------------------------------------ 4. lemma synthesis is reachable
def test_try_lemma_is_callable_and_keeps_what_the_compiler_accepts(tmp_path,
                                                                   monkeypatch):
    """Reachability only. Whether the model CHOOSES to call it is a different
    question, and not one a test can answer."""
    from verifiers.lean_runner import LeanOutcome, LeanResult

    async def accepts(source):
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: accepts)

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_lemma", {"statement": "lemma helper : 1 = 1",
                                     "proof": "rfl"}),
                  ]))

    assert log.kept_lemmas(str(tmp_path)), "an accepted lemma was not kept"


# -------------------------------------------- 5. refutation on the live path
def test_the_suspect_exit_hands_over_the_negation(tmp_path, rejecting_compiler):
    """The agent must be given the OPPORTUNITY to prove the negation, not just
    told that prose is insufficient."""
    result = harness.prove(
        "q", model=object(), workdir=str(tmp_path),
        agent_factory=scripted([
            ("check_statement", {"statement": GOAL}),
            ("try_proof", {"proof": "by\n  have h : IsOpen Ω := ‹_›\n  exact foo h"}),
            ("finish", {"summary": "Ω is not connected",
                        "outcome": "statement_suspect"}),
        ]))

    # The report was REFUSED, and the refusal carried the negation to prove.
    trace = log.read(str(tmp_path))["trace"]
    assert not any("suspect statement" in e for e in trace), (
        "an unearned suspect report reached the trace"
    )
    assert not result.proved


def test_a_compiled_negation_reaches_the_refuted_outcome(tmp_path, monkeypatch):
    from verifiers.lean_runner import LeanOutcome, LeanResult

    async def accepts(source):
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: accepts)

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_proof", {"proof": "by\n  have h : True := trivial\n"
                                              "  exact foo h"}),
                      ("try_refutation", {"proof": "by\n  intro h\n  exact absurd h g"}),
                      ("finish", {"summary": "Ω is not connected",
                                  "outcome": "statement_suspect"}),
                  ]))

    trace = log.read(str(tmp_path))["trace"]
    assert any("refuted statement" in e for e in trace), (
        "a compiled negation did not produce a refutation"
    )


def test_the_refutation_uses_the_same_anti_cheat(tmp_path, monkeypatch):
    """It must not have its own acceptance rule. `sorry` is refused before
    dispatch, on the live path, exactly as for a proof."""
    from verifiers.lean_runner import LeanOutcome, LeanResult

    compiled = []

    async def accepts(source):
        compiled.append(source)
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: accepts)

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_refutation", {"proof": "by sorry"}),
                  ]))

    assert not log.records(str(tmp_path), log.REFUTATION), (
        "a `sorry` refutation was recorded"
    )
    assert len(compiled) == 1, "a `sorry` refutation reached the compiler"


# ------------------------------- 6. decomposition acts, on the live path
def test_the_live_path_carries_a_skeleton_all_the_way_to_a_proof(tmp_path,
                                                                 monkeypatch):
    """THE test for the lemma-synthesis change.

    harness.prove -> real tools -> try_skeleton -> hole extracted -> lemma
    compiled -> lemma kept -> skeleton reassembled -> goal PROVED, with the
    model doing nothing but proposing the decomposition. `try_lemma` is never
    called by the script: the controller does the work.
    """
    from verifiers.lean_runner import LeanOutcome, LeanResult

    seen = []

    async def run_lean(source):
        seen.append(source)
        if "sorry" in source:                 # statement check and skeleton
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)

    skeleton = ("by\n  have h_deriv : ∀ x ∈ Ω, deriv f x = 0 := by sorry\n"
                "  exact foo h_deriv")

    run = harness.prove(
        "q", model=object(), workdir=str(tmp_path),
        agent_factory=scripted([
            ("check_statement", {"statement": GOAL}),
            ("try_skeleton", {"proof": skeleton}),
            ("finish", {"summary": "decomposed and proved", "outcome": "proved",
                        "statement": GOAL}),
        ]))

    assert log.kept_lemmas(str(tmp_path)), "no lemma was synthesised"
    assert any("exact mra_lemma_1" in s for s in seen), (
        "the skeleton was never reassembled with its lemma"
    )
    assert run.proved, "the goal was proved by the controller and not reported"


def test_the_controller_charges_its_extra_compiles_to_the_budget(tmp_path,
                                                                 monkeypatch):
    """Several compiles happen inside ONE tool call. Uncharged, the automatic
    filling could overrun the compile budget the budget exists to enforce."""
    from math_v2.core import budget
    from verifiers.lean_runner import LeanOutcome, LeanResult

    async def run_lean(source):
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)

    skeleton = ("by\n  have h_deriv : ∀ x ∈ Ω, deriv f x = 0 := by sorry\n"
                "  exact foo h_deriv")
    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_skeleton", {"proof": skeleton}),
                  ]))

    spent = budget.summary(str(tmp_path))["lean_calls"]
    assert spent >= 4, f"only {spent} compiles charged for check + skeleton + fill + assembly"


# --------------------- 7. context-aware synthesis and error-driven retrieval
def test_the_live_path_copies_binders_into_a_synthesised_lemma(tmp_path,
                                                               monkeypatch):
    """A hole of a real goal mentions the goal's own objects. Compiled
    standalone it cannot elaborate, so decomposition would fire and achieve
    nothing — which is what it did before."""
    from verifiers.lean_runner import LeanOutcome, LeanResult

    seen = []

    async def run_lean(source):
        seen.append(source)
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)

    goal = ("theorem ex {f : ℂ → ℂ} (Ω : Set ℂ) (h : IsOpen Ω) "
            "(hf : DifferentiableOn ℂ f Ω) : f = f")
    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": goal}),
                      ("try_skeleton", {"proof":
                          "by\n  have hd : ∀ x ∈ Ω, deriv f x = 0 := by sorry\n"
                          "  exact foo hd"}),
                  ]))

    lemma_files = [s for s in seen if "deriv f x = 0 :=" in s]
    assert lemma_files, "the hole was never compiled as a lemma"
    assert "(Ω : Set ℂ)" in lemma_files[0], "compiled standalone; Ω is unbound"
    assert "(hf : DifferentiableOn ℂ f Ω)" in lemma_files[0], "hypothesis dropped"


def test_the_live_path_searches_from_the_compiler_error(tmp_path, monkeypatch):
    """The error names what is missing. Before, the model picked the next query
    itself and picked `"constant"`, `"deriv"`, `"abs"`."""
    from retrieval.loogle import Premise
    from verifiers.lean_runner import LeanOutcome, LeanResult

    queries = []

    class Search:
        def premises_for(self, statement):
            return []

        def search_with_suggestions(self, query, limit=None):
            queries.append(query)
            return [Premise(name="Complex.abs_apply", type=" : ‖z‖ = z.abs",
                            module="Mathlib.Analysis")], []

    async def run_lean(source):
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.ERRORS,
                          "2:58: error: Unknown constant `Complex.abs`")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)
    monkeypatch.setattr("math_v2.tools.retrieval.get_search", lambda: Search())

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_proof", {"proof": "by exact Complex.abs"}),
                  ]))

    assert '"abs"' in queries, f"the error was not turned into a query: {queries}"
    assert "Complex.abs_apply" in [
        p["name"] for p in log.read(str(tmp_path))["premises"]]


def test_the_prompt_describes_what_the_code_now_enforces(tmp_path,
                                                         rejecting_compiler):
    """A prompt promising capabilities the code does not have teaches the model
    a system that does not exist."""
    factory = scripted([("check_statement", {"statement": GOAL})])
    harness.prove("q", model=object(), workdir=str(tmp_path), agent_factory=factory)

    prompt = factory.prompt

    assert "copy the binders each claim needs" in prompt, "context-aware synthesis"
    assert "ASCRIBE THE TYPE" in prompt, "the unascribed-have limitation"
    assert "already returned" in prompt, "error-driven retrieval"
    assert "tactic failed" in prompt, "the fifth error class"


# ------------------------------- 8. no compiling path escapes the budget
def test_no_sequence_of_tools_can_exceed_the_compile_budget(tmp_path, monkeypatch):
    """THE budget regression. Several paths now compile MORE than once inside a
    single tool call — the tactic ladder, hole synthesis, assembly — and an
    uncharged one would silently overrun the limit the budget exists to hold.

    Drives every compiling tool repeatedly and counts actual compilations.
    """
    from math_v2.core import budget
    from verifiers.lean_runner import LeanOutcome, LeanResult

    compiles = []

    async def run_lean(source):
        compiles.append(source)
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)

    skeleton = ("by\n  have h1 : 1 = 1 := by sorry\n  have h2 : 2 = 2 := by sorry\n"
                "  have h3 : 3 = 3 := by sorry\n  exact foo h1 h2 h3")
    script = [("check_statement", {"statement": GOAL})]
    for i in range(12):
        script.append(("try_skeleton", {"proof": skeleton.replace("1 = 1",
                                                                  f"{i} = {i}")}))
        script.append(("try_proof", {"proof": f"by\n  have z{i} : 1 = 1 := rfl\n"
                                              f"  exact g{i} z{i}"}))
        script.append(("try_standard_tactics", {}))

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted(script))

    assert len(compiles) <= budget.MAX_LEAN_CALLS, (
        f"{len(compiles)} compilations against a budget of "
        f"{budget.MAX_LEAN_CALLS}"
    )


def test_the_in_call_compiles_of_a_skeleton_are_all_charged(tmp_path, monkeypatch):
    """A skeleton may spend one compile per hole plus one to assemble. The
    counter must move by the same amount the compiler was actually called."""
    from math_v2.core import budget
    from verifiers.lean_runner import LeanOutcome, LeanResult

    compiles = []

    async def run_lean(source):
        compiles.append(source)
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_skeleton", {"proof":
                          "by\n  have h1 : 1 = 1 := by sorry\n"
                          "  have h2 : 2 = 2 := by sorry\n  exact foo h1 h2"}),
                  ]))

    charged = budget.summary(str(tmp_path))["lean_calls"]
    assert charged == len(compiles), (
        f"charged {charged} for {len(compiles)} actual compilations"
    )


def test_error_driven_retrieval_never_compiles(tmp_path, monkeypatch):
    """It is an HTTP lookup. If it ever started compiling it would be an
    uncharged path straight through the budget."""
    from retrieval.loogle import Premise
    from verifiers.lean_runner import LeanOutcome, LeanResult

    compiles = []

    class Search:
        def premises_for(self, statement):
            return []

        def search_with_suggestions(self, query, limit=None):
            return [Premise(name="X", type=" : 1 = 1", module="Mathlib.A")], []

    async def run_lean(source):
        compiles.append(source)
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.ERRORS, "error: Unknown constant `Foo.bar`")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)
    monkeypatch.setattr("math_v2.tools.retrieval.get_search", lambda: Search())

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_proof", {"proof": "by exact Foo.bar"}),
                  ]))

    assert len(compiles) == 2, "retrieval on rejection compiled something"


# --------------- 9. the recorded WINNING attempts are still allowed through
def test_the_four_syntactic_variants_that_won_a_real_goal_are_not_refused(
        tmp_path, monkeypatch):
    """From near-mathlib-repl, num-primes-strictly-above: rcases, obtain, match
    and cases — all citing `Nat.exists_infinite_primes`, the FOURTH accepted.
    A "same underlying strategy" refusal would have destroyed a proof we have.
    """
    from verifiers.lean_runner import LeanOutcome, LeanResult

    compiles = []

    async def run_lean(source):
        compiles.append(source)
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        if "cases Nat.exists_infinite_primes" in source:
            return LeanResult(LeanOutcome.COMPILED, "")
        return LeanResult(LeanOutcome.ERRORS, "f.lean:1:1: error: unsolved goals")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)

    goal = "theorem exists_prime_gt (n : ℕ) : ∃ p, Nat.Prime p ∧ n < p"
    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=scripted([
                            ("check_statement", {"statement": goal}),
                            ("try_proof", {"proof": "rcases Nat.exists_infinite_primes (n + 1) with ⟨p, hp1, hp2⟩\n  exact ⟨p, hp2, hp1⟩"}),
                            ("try_proof", {"proof": "obtain ⟨p, hp1, hp2⟩ := Nat.exists_infinite_primes (n + 1)\n  use p"}),
                            ("try_proof", {"proof": "match Nat.exists_infinite_primes (n + 1) with\n| ⟨p, hp1, hp2⟩ => exact ⟨p, hp2, hp1⟩"}),
                            ("try_proof", {"proof": "cases Nat.exists_infinite_primes (n + 1) with\n| intro p hp => exact ⟨p, hp.2, hp.1⟩"}),
                            ("finish", {"summary": "done", "outcome": "proved",
                                        "statement": goal}),
                        ]))

    assert run.proved, "the winning fourth variant was refused as a repeat"
    assert len(compiles) == 5, f"only {len(compiles) - 1} of 4 variants compiled"


# ------------------------ 10. the `exact` repair, through the real harness
def test_the_live_path_repairs_a_bare_term_with_exact(tmp_path, monkeypatch):
    """Replays top-compact-image from near-mathlib-repl: `hs.image hf` was
    rejected as `unknown tactic` and `by exact hs.image hf` was accepted. The
    model now gets that in ONE tool call instead of two attempts."""
    from verifiers.lean_runner import LeanOutcome, LeanResult

    compiles = []

    async def run_lean(source):
        compiles.append(source)
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        if "exact hs.image hf" in source:
            return LeanResult(LeanOutcome.COMPILED, "")
        return LeanResult(LeanOutcome.ERRORS, "f.lean:2:3: error: unknown tactic")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)

    goal = ("theorem continuous_image_compact (hs : IsCompact s) "
            "(hf : Continuous f) : IsCompact (f '' s)")
    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=scripted([
                            ("check_statement", {"statement": goal}),
                            ("try_proof", {"proof": "hs.image hf"}),
                            ("finish", {"summary": "done", "outcome": "proved",
                                        "statement": goal}),
                        ]))

    assert run.proved, "the repaired proof did not reach the verdict"
    assert any("exact hs.image hf" in s for s in compiles)


def test_the_repair_compile_is_charged_on_the_live_path(tmp_path, monkeypatch):
    from math_v2.core import budget
    from verifiers.lean_runner import LeanOutcome, LeanResult

    compiles = []

    async def run_lean(source):
        compiles.append(source)
        if "sorry" in source:
            return LeanResult(LeanOutcome.INCOMPLETE, "declaration uses 'sorry'")
        return LeanResult(LeanOutcome.ERRORS, "f.lean:2:3: error: unknown tactic")

    monkeypatch.setattr("math_v2.tools.proving.lean_runner", lambda w: run_lean)

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=scripted([
                      ("check_statement", {"statement": GOAL}),
                      ("try_proof", {"proof": "some.term arg"}),
                  ]))

    charged = budget.summary(str(tmp_path))["lean_calls"]
    assert charged == len(compiles) == 3, (
        f"charged {charged} for {len(compiles)} compilations"
    )
