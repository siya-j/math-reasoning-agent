"""Offline tests for the Lean verifier (Phase 6).

None of these require Lean to be installed. The runner is stubbed, because
what is being tested is the POLICY — what each compiler outcome means — and
that is where a soundness bug would live.

The single most important test in this file is
`test_a_rejected_proof_is_unknown_never_false`.
"""

import pytest

from domain.verdict import VerificationStatus as S
from domain.verification import VerificationKind, VerificationRequest
from verifiers.lean_runner import LeanOutcome, LeanResult, lean_is_available
from verifiers.lean_verifier import LeanVerifier, build_source

STATEMENT = "theorem infinitude_of_primes : ∀ n : ℕ, ∃ p, n < p ∧ Nat.Prime p"
PROOF = "exact Nat.exists_infinite_primes"


def request(statement=STATEMENT, proof=PROOF):
    return VerificationRequest(
        kind=VerificationKind.FORMAL, statement=statement, proof=proof
    )


def verifier_returning(outcome, output=""):
    return LeanVerifier(runner=lambda source: LeanResult(outcome, output))


# --------------------------------------------------------------- the rule
def test_a_rejected_proof_is_unknown_never_false():
    """Lean failing to prove P is not evidence against P.

    If this ever returns FALSE, the system will start refuting true
    theorems. This is the most important assertion in the project.
    """
    verdict = verifier_returning(
        LeanOutcome.ERRORS, "Claim.lean:3:0: error: unknown identifier 'foo'"
    ).verify(request())
    assert verdict.status is S.UNKNOWN
    assert verdict.status is not S.FALSE
    assert not verdict.was_verified


def test_the_rejection_message_says_unproved_not_false():
    verdict = verifier_returning(LeanOutcome.ERRORS, "error: bad").verify(request())
    assert "does NOT make the claim false" in verdict.detail


def test_an_accepted_proof_is_true():
    verdict = verifier_returning(LeanOutcome.COMPILED).verify(request())
    assert verdict.status is S.TRUE
    assert verdict.method == "lean"


# ------------------------------------------------------------ the `sorry` trap
def test_a_proof_using_sorry_proves_nothing():
    """`sorry` compiles with exit code 0 and establishes precisely nothing."""
    verdict = verifier_returning(LeanOutcome.INCOMPLETE).verify(request())
    assert verdict.status is S.UNKNOWN
    assert "sorry" in verdict.detail


def test_placeholders_are_detected_in_the_source_even_without_a_warning():
    """Belt and braces: do not rely on the compiler warning us."""
    from verifiers.lean_runner import _uses_placeholder

    assert _uses_placeholder("theorem t : True := by sorry", "")
    assert _uses_placeholder("theorem t : True := by admit", "")
    assert _uses_placeholder("theorem t : True := by trivial", "declaration uses 'sorry'")
    assert not _uses_placeholder("theorem t : True := by trivial", "")


def test_a_word_containing_sorry_is_not_a_placeholder():
    """`sorry` as a substring must not refuse a legitimate proof."""
    from verifiers.lean_runner import _uses_placeholder

    assert not _uses_placeholder("theorem sorryless : True := by trivial", "")


# ------------------------------------------------- compiles but proves nothing
def test_an_axiom_declaration_is_not_a_proof():
    """Assuming the goal compiles perfectly and establishes nothing."""
    from verifiers.lean_runner import cheating_devices

    devices = cheating_devices("axiom cheat : 2 + 2 = 5\ntheorem t : 2 + 2 = 5 := cheat")
    assert devices and "axiom" in devices[0]


def test_suggestion_tactics_are_not_proofs():
    """`apply?` reports candidates; it does not commit to a proof."""
    from verifiers.lean_runner import cheating_devices

    assert cheating_devices("theorem t : True := by apply?")
    assert cheating_devices("theorem t : True := by exact?")
    assert cheating_devices("theorem t : True := by simp?")


def test_an_honest_proof_uses_no_devices():
    from verifiers.lean_runner import cheating_devices

    assert cheating_devices("theorem t : 2 + 2 = 4 := by norm_num") == []
    assert cheating_devices("theorem t : True := by exact trivial") == []


def test_a_cheated_proof_is_unknown_not_true():
    verdict = verifier_returning(
        LeanOutcome.CHEATED, "an `axiom` declaration, which assumes rather than proves"
    ).verify(request())
    assert verdict.status is S.UNKNOWN
    assert verdict.status is not S.TRUE
    assert "not the same as proving" in verdict.detail


def test_every_compiler_error_is_reported_not_just_the_first():
    """Refinement is only as good as the feedback it gets."""
    output = "a.lean:1:0: error: first\na.lean:2:0: error: second"
    verdict = verifier_returning(LeanOutcome.ERRORS, output).verify(request())
    assert "first" in verdict.detail
    assert "second" in verdict.detail


UNSOLVED = """Claim.lean:5:2: error: unsolved goals
case h
G : Type u_1
inst✝ : Group G
⊢ IsCyclic G"""


def test_the_goal_state_reaches_the_refinement_feedback():
    """The measured gap: collecting only lines containing `error:` discarded
    the goal, so refinement was told "unsolved goals" without being told
    WHICH goal — the most useful thing the compiler had to say."""
    verdict = verifier_returning(LeanOutcome.ERRORS, UNSOLVED).verify(request())
    assert "⊢ IsCyclic G" in verdict.detail
    assert "G : Type u_1" in verdict.detail


def test_the_remaining_goals_are_restated_plainly():
    """Buried in a wall of context they are missed; stated plainly they are
    the instruction."""
    verdict = verifier_returning(LeanOutcome.ERRORS, UNSOLVED).verify(request())
    assert "Still to prove:" in verdict.detail


def test_an_error_block_stops_at_the_next_diagnostic():
    from verifiers.lean_runner import LeanResult

    output = UNSOLVED + "\nClaim.lean:9:0: warning: declaration uses 'sorry'"
    blocks = LeanResult(LeanOutcome.ERRORS, output).errors

    assert len(blocks) == 1
    assert "declaration uses" not in blocks[0], "a warning leaked into the block"
    assert "⊢ IsCyclic G" in blocks[0]


# --------------------------------------------------------- honest degradation
def test_missing_lean_is_unknown_not_a_crash():
    verdict = verifier_returning(LeanOutcome.UNAVAILABLE).verify(request())
    assert verdict.status is S.UNKNOWN
    assert "not installed" in verdict.detail


def test_a_timeout_is_unknown_not_false():
    verdict = verifier_returning(LeanOutcome.TIMEOUT).verify(request())
    assert verdict.status is S.UNKNOWN
    assert "Slow is not false" in verdict.detail


def test_a_runner_that_explodes_does_not_take_the_pipeline_down():
    def broken(source):
        raise RuntimeError("disk on fire")

    verdict = LeanVerifier(runner=broken).verify(request())
    assert verdict.status is S.UNKNOWN
    assert "disk on fire" in verdict.detail


def test_no_proof_supplied_is_refused_with_an_honest_reason():
    verdict = LeanVerifier(runner=lambda s: LeanResult(LeanOutcome.COMPILED)).verify(
        request(proof="")
    )
    assert verdict.status is S.UNKNOWN
    assert "does not search" in verdict.detail


def test_no_statement_supplied_is_refused():
    verdict = verifier_returning(LeanOutcome.COMPILED).verify(request(statement=""))
    assert verdict.status is S.UNKNOWN


# ------------------------------------------------------- Mathlib resolution
def test_a_configured_lake_project_is_used_to_run_lean(tmp_path, monkeypatch):
    """`import Mathlib` only resolves via `lake env lean` inside the project."""
    import config
    from verifiers import lean_runner

    monkeypatch.setattr(config, "LEAN_PROJECT", str(tmp_path))
    monkeypatch.setattr(lean_runner.shutil, "which", lambda command: "/usr/bin/lean")

    seen = {}

    class Completed:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        return Completed()

    monkeypatch.setattr(lean_runner.subprocess, "run", fake_run)
    lean_runner.run_lean("theorem t : True := by trivial\n")

    assert seen["argv"][:3] == ["lake", "env", "lean"]
    assert seen["cwd"] == str(tmp_path)


def test_without_a_project_lean_is_invoked_directly(monkeypatch):
    import config
    from verifiers import lean_runner

    monkeypatch.setattr(config, "LEAN_PROJECT", "")
    monkeypatch.setattr(lean_runner.shutil, "which", lambda command: "/usr/bin/lean")

    seen = {}

    class Completed:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return Completed()

    monkeypatch.setattr(lean_runner.subprocess, "run", fake_run)
    lean_runner.run_lean("theorem t : True := by trivial\n")

    assert seen["argv"][0] == "lean"
    assert "lake" not in seen["argv"]


# ------------------------------------------------------------------ routing
def test_lean_only_claims_formal_requests():
    lean = LeanVerifier()
    assert lean.supports(request())
    assert not lean.supports(
        VerificationRequest(kind=VerificationKind.EQUALITY, lhs="x", rhs="x")
    )


def test_the_registry_routes_formal_requests_to_lean():
    """Principle 8: adding a verifier changed one line and nothing else."""
    from verifiers import verify

    verdict = verify(request())
    assert verdict.method == "lean"


def test_sympy_still_handles_its_own_requests():
    from verifiers import verify

    verdict = verify(
        VerificationRequest(kind=VerificationKind.NUMERIC, lhs="2 + 2", rhs="4")
    )
    assert verdict.method == "sympy"
    assert verdict.status is S.TRUE


# ------------------------------------------------------------ source assembly
def test_source_includes_mathlib_and_the_theorem():
    source = build_source(STATEMENT, PROOF)
    assert "import Mathlib" in source
    assert STATEMENT in source
    assert PROOF in source


def test_a_tactic_proof_is_wrapped_in_by():
    assert " by" in build_source("theorem t : True", "trivial")


def test_an_explicit_by_block_is_not_double_wrapped():
    source = build_source("theorem t : True", "by trivial")
    assert source.count("by") == 1


# ------------------------------------- the real thing, when it is available
@pytest.mark.skipif(not lean_is_available(), reason="Lean is not installed")
def test_end_to_end_against_a_real_lean_installation():
    from verifiers.lean_runner import run_lean

    good = run_lean("theorem trivially_true : True := by trivial\n")
    assert good.outcome is LeanOutcome.COMPILED

    bad = run_lean("theorem broken : True := by exact nonsense_lemma\n")
    assert bad.outcome is LeanOutcome.ERRORS

    placeholder = run_lean("theorem lazy : True := by sorry\n")
    assert placeholder.outcome is LeanOutcome.INCOMPLETE
