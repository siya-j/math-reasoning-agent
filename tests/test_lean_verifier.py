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
from verifiers.lean_runner import LeanOutcome, LeanResult, lean_toolchain_works
from verifiers.lean_verifier import LeanVerifier, build_source, rename_goal

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
    """No captured detail (the genuine "nothing on this machine" case) still
    falls back to a message, never a crash."""
    verdict = verifier_returning(LeanOutcome.UNAVAILABLE).verify(request())
    assert verdict.status is S.UNKNOWN
    assert verdict.detail


def test_unavailable_surfaces_the_real_reason_not_a_generic_guess():
    """MEASURED: `_util.py` wraps BOTH the REPL and subprocess paths in one
    `except Exception`, reporting UNAVAILABLE for a wedged REPL session, a
    subprocess that could not spawn, or an actually-missing binary alike —
    and always records which one really happened in `output`. A run failing
    because a REPL session had crashed used to print "Lean is not installed
    on this machine" even though Lean plainly was, with no way to tell the
    causes apart without reading source."""
    real_reason = "Lean could not be run: the REPL did not answer within 180s"
    verdict = verifier_returning(LeanOutcome.UNAVAILABLE, real_reason).verify(request())
    assert verdict.status is S.UNKNOWN
    assert verdict.detail == real_reason
    assert "not installed" not in verdict.detail


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
    # The declared name is replaced (see rename_goal); the rest is verbatim.
    assert rename_goal(STATEMENT) in source
    assert PROOF in source


def test_a_tactic_proof_is_wrapped_in_by():
    assert " by" in build_source("theorem t : True", "trivial")


def test_an_explicit_by_block_is_not_double_wrapped():
    source = build_source("theorem t : True", "by trivial")
    assert source.count("by") == 1


def test_every_line_of_a_multiline_proof_is_indented_not_just_the_first():
    """MEASURED, `hard-sophie-germain`: a real skeleton submitted as

        rcases n with _ | n
        · contradiction
        · rcases n with _ | m
          · contradiction
          · sorry

    used to become, after wrapping:

        theorem mra_goal ... := by
          rcases n with _ | n
        · contradiction
        · rcases n with _ | m
          · contradiction
          · sorry

    `rcases` at column 2, every bullet at column 0 — shallower than the
    tactic that opened the block, which ends it right there in Lean 4's
    indentation-significant syntax. Both resulting goals were reported
    "unsolved" no matter what the bullets said, indistinguishable from a
    genuinely wrong tactic. Every line must sit at the same column as
    `rcases`, and a bullet nested one level deeper in the ORIGINAL proof
    must stay one level deeper here too, not collapse to the same column
    as its parent.
    """
    proof = (
        "rcases n with _ | n\n"
        "· contradiction\n"
        "· rcases n with _ | m\n"
        "  · contradiction\n"
        "  · sorry"
    )
    source = build_source("theorem t (n : Nat) : True", proof)
    lines = source.splitlines()

    rcases_line = next(l for l in lines if "rcases n with _ | n" in l)
    top_bullet = next(l for l in lines if l.strip() == "· contradiction")
    nested_bullet = next(l for l in lines if l.strip() == "· sorry")

    def col(line):
        return len(line) - len(line.lstrip(" "))

    assert col(top_bullet) == col(rcases_line), (
        "a sibling bullet must sit at the same column as the tactic "
        "that opened the block"
    )
    assert col(nested_bullet) == col(top_bullet) + 2, (
        "a bullet nested one level deeper in the SUBMITTED proof must "
        "stay one level deeper here, not collapse onto its parent"
    )


# ------------------------------------- the real thing, when it is available
# GATED ON `lean_toolchain_works`, NOT `lean_is_available`. MEASURED: with elan
# installed but no toolchain configured, `lean_is_available` is True, so this
# test did not skip -- it FAILED, with "no default toolchain configured". Read
# as environmental noise and deselected run after run, which is how the only
# test guarding the compiler's own behaviour stopped running at all. A check
# that cannot run must skip loudly rather than fail quietly.
#
# tests/test_lean_real.py is the thorough version of this; this stays as the
# smoke test.
@pytest.mark.skipif(
    not lean_toolchain_works(),
    reason="Lean has no usable toolchain (see lean_toolchain_works)",
)
def test_end_to_end_against_a_real_lean_installation():
    from verifiers.lean_runner import run_lean

    good = run_lean("theorem trivially_true : True := by trivial\n")
    assert good.outcome is LeanOutcome.COMPILED

    bad = run_lean("theorem broken : True := by exact nonsense_lemma\n")
    assert bad.outcome is LeanOutcome.ERRORS

    placeholder = run_lean("theorem lazy : True := by sorry\n")
    assert placeholder.outcome is LeanOutcome.INCOMPLETE


# --------------------------------------------- the goal must not shadow Mathlib
def test_the_goal_is_renamed_so_it_cannot_collide_with_the_library():
    """Measured: `theorem irrational_sqrt_two` made its own proof impossible.

        error: `irrational_sqrt_two` has already been declared

    The collision is at DECLARATION, so no proof can work around it — and a
    GOOD formalizer hits this more often, because the right name for a known
    theorem is the library's name.
    """
    source = build_source(
        "theorem irrational_sqrt_two : Irrational (Real.sqrt 2)",
        "exact irrational_sqrt_two",
    )
    assert "theorem mra_goal :" in source
    assert "exact irrational_sqrt_two" in source, "the PROOF must not be rewritten"


def test_renaming_keeps_binders_and_a_trailing_assignment():
    assert rename_goal("lemma foo (n : Nat) : n + 0 = n") == (
        "lemma mra_goal (n : Nat) : n + 0 = n"
    )
    assert rename_goal("theorem t : True :=").endswith(":=")


def test_renaming_leaves_a_statement_with_no_declaration_alone():
    assert rename_goal("example : True") == "example : True"


# ------------------------------------------------------------ the real-Lean gate
# OFFLINE, and living here rather than in tests/test_lean_real.py, because that
# file's module-level `pytestmark` skips everything in it wherever Lean cannot
# run -- which would have skipped these too, on exactly the machines where
# getting the gate right matters most.
class _Probe:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""


def test_the_gate_rejects_an_unconfigured_toolchain(monkeypatch):
    """THE case that made this file necessary. `which lean` finds the elan
    shim and `lean --version` EXITS 0 while printing the toolchain error, so
    neither the path check nor the return code can be trusted -- both measured
    on the machine this was written on."""
    from verifiers import lean_runner

    monkeypatch.setattr(lean_runner, "_TOOLCHAIN_WORKS", {})
    monkeypatch.setattr(lean_runner.shutil, "which", lambda name: "/x/lean")
    monkeypatch.setattr(lean_runner.subprocess, "run", lambda *a, **k: _Probe(
        "error: no default toolchain configured. run `elan default stable`"))

    assert lean_runner.lean_toolchain_works("lean") is False


def test_the_gate_accepts_a_working_toolchain(monkeypatch):
    from verifiers import lean_runner

    monkeypatch.setattr(lean_runner, "_TOOLCHAIN_WORKS", {})
    monkeypatch.setattr(lean_runner.shutil, "which", lambda name: "/x/lean")
    monkeypatch.setattr(lean_runner.subprocess, "run",
                        lambda *a, **k: _Probe("Lean (version 4.33.0)"))

    assert lean_runner.lean_toolchain_works("lean") is True


def test_the_gate_says_no_when_lean_is_absent_without_probing(monkeypatch):
    from verifiers import lean_runner

    monkeypatch.setattr(lean_runner, "_TOOLCHAIN_WORKS", {})
    monkeypatch.setattr(lean_runner.shutil, "which", lambda name: None)

    def explode(*a, **k):
        raise AssertionError("probed a binary that is not there")

    monkeypatch.setattr(lean_runner.subprocess, "run", explode)

    assert lean_runner.lean_toolchain_works("lean") is False


def test_a_broken_probe_is_not_a_working_toolchain(monkeypatch):
    """A timeout or an OSError must read as "cannot compile", never as an
    exception escaping into collection -- a `skipif` that raises takes the
    whole test session down."""
    from verifiers import lean_runner

    for boom in (OSError("nope"), lean_runner.subprocess.TimeoutExpired("lean", 1)):
        monkeypatch.setattr(lean_runner, "_TOOLCHAIN_WORKS", {})
        monkeypatch.setattr(lean_runner.shutil, "which", lambda name: "/x/lean")

        def raiser(*a, **k):
            raise boom

        monkeypatch.setattr(lean_runner.subprocess, "run", raiser)

        assert lean_runner.lean_toolchain_works("lean") is False


def test_the_probe_is_cached_so_collection_does_not_respawn_it(monkeypatch):
    """`skipif` at module scope evaluates on every collection, and this spawns
    a process."""
    from verifiers import lean_runner

    calls = []
    monkeypatch.setattr(lean_runner, "_TOOLCHAIN_WORKS", {})
    monkeypatch.setattr(lean_runner.shutil, "which", lambda name: "/x/lean")
    monkeypatch.setattr(lean_runner.subprocess, "run", lambda *a, **k: (
        calls.append(1), _Probe("Lean (version 4.33.0)"))[1])

    lean_runner.lean_toolchain_works("lean")
    lean_runner.lean_toolchain_works("lean")

    assert len(calls) == 1


# --------------------------------------------- the Mathlib-reachability gate
# MEASURED, and the reason this gate exists as well as the toolchain one: on a
# machine with a perfectly healthy Lean but `MRA_LEAN_PROJECT` unset, all
# eleven real-Lean tests ran against bare `lean`. Seven failed with "unknown
# module prefix 'Mathlib'" and FOUR PASSED FOR THE WRONG REASON, because an
# assertion of the form "this did not compile" is satisfied by any
# infrastructure fault. A gate proving Lean RUNS does not prove Mathlib is
# REACHABLE.
def test_mathlib_reachability_requires_an_actual_compile(monkeypatch):
    """`import Mathlib` resolving is not inferable from the binary or the
    version -- it depends on a Lake project, so it has to be compiled."""
    from verifiers import lean_runner

    monkeypatch.setattr(lean_runner, "_MATHLIB_AVAILABLE", {})
    monkeypatch.setattr(lean_runner, "lean_toolchain_works", lambda: True)
    # `**_` because the probe passes `timeout=LEAN_COLD_TIMEOUT`: a cold
    # `import Mathlib` does not finish inside the default 60s, and a double
    # that cannot accept the keyword hides that the real call changed.
    monkeypatch.setattr(lean_runner, "run_lean",
                        lambda source, **_: LeanResult(LeanOutcome.COMPILED))

    assert lean_runner.mathlib_is_available() is True


def test_an_unresolved_mathlib_import_is_not_availability(monkeypatch):
    """The exact observed failure: Lean runs, the import does not resolve."""
    from verifiers import lean_runner

    monkeypatch.setattr(lean_runner, "_MATHLIB_AVAILABLE", {})
    monkeypatch.setattr(lean_runner, "lean_toolchain_works", lambda: True)
    monkeypatch.setattr(lean_runner, "run_lean", lambda source, **_: LeanResult(
        LeanOutcome.ERRORS, "error: unknown module prefix 'Mathlib'"))

    assert lean_runner.mathlib_is_available() is False


def test_mathlib_is_not_probed_when_lean_cannot_run(monkeypatch):
    """No point spending a compile to discover what the cheaper gate knows."""
    from verifiers import lean_runner

    monkeypatch.setattr(lean_runner, "_MATHLIB_AVAILABLE", {})
    monkeypatch.setattr(lean_runner, "lean_toolchain_works", lambda: False)

    def explode(source, **_):
        raise AssertionError("compiled despite Lean being unusable")

    monkeypatch.setattr(lean_runner, "run_lean", explode)

    assert lean_runner.mathlib_is_available() is False


def test_the_mathlib_probe_is_cached(monkeypatch):
    """It is a real compile, and a module-scope `skipif` re-evaluates on every
    collection."""
    from verifiers import lean_runner

    calls = []
    monkeypatch.setattr(lean_runner, "_MATHLIB_AVAILABLE", {})
    monkeypatch.setattr(lean_runner, "lean_toolchain_works", lambda: True)
    monkeypatch.setattr(lean_runner, "run_lean", lambda source, **_: (
        calls.append(source), LeanResult(LeanOutcome.COMPILED))[1])

    lean_runner.mathlib_is_available()
    lean_runner.mathlib_is_available()

    assert len(calls) == 1


# ------------------------------------------ the harness must not disable itself
def test_the_conftest_preserves_where_the_compiler_lives():
    """MEASURED, and it silently disabled every real-Lean test in the repo.

    `conftest.pytest_configure` clears every `MRA_*` variable, for a good
    reason its own docstring records: a leaked benchmark environment
    (`MRA_EXEC`, `MRA_PROVER`, `MRA_MAX_AGENT_SECONDS`) once made six tests
    fail by changing the behaviour under test.

    But `MRA_LEAN_PROJECT` is a different kind of variable. It names a path on
    the machine, and clearing it cannot restore a declared default -- there is
    no default location for a Lake project. So `tests/test_lean_real.py`
    probed with bare `lean`, got "unknown module prefix 'Mathlib'", and
    skipped all eleven of its tests IN A SHELL WHERE THE OPERATOR HAD JUST
    EXPORTED THE VARIABLE IN ORDER TO RUN THEM.

    The rule the exemption encodes: clear what changes BEHAVIOUR, keep what
    says WHERE AN EXTERNAL TOOL IS.
    """
    from tests.conftest import _LOCATION_ONLY

    assert "MRA_LEAN_PROJECT" in _LOCATION_ONLY
    assert "MRA_LEAN" in _LOCATION_ONLY

    # The variables that motivated the clearing must never be exempted.
    for behaviour in ("MRA_PROVER", "MRA_EXEC", "MRA_MAX_AGENT_SECONDS",
                      "MRA_MAX_AGENT_LEAN", "MRA_LEAN_TIMEOUT"):
        assert behaviour not in _LOCATION_ONLY, behaviour


def test_an_exported_lean_project_survives_into_the_tests(request):
    """The end-to-end half, and it compares against what the shell ORIGINALLY
    held rather than against what is there now.

    The first version of this test read `os.environ` and skipped when the
    variable was missing -- which is precisely the state the bug produces, so
    it skipped instead of failing and could never have caught anything. That
    is the same "silence reads as success" trap as a negative assertion
    against a compiler that never ran. `conftest.ORIGINAL_ENVIRONMENT` is
    captured before any clearing, so "the operator set nothing" and "the
    harness deleted it" are now distinguishable, and only the first is a skip.
    """
    import os

    # `request.config`, not a conftest import: importing that file again makes
    # a SECOND module object, created after the clearing, whose idea of the
    # "original" environment is the already-cleared one. That mistake made the
    # previous version of this test skip on exactly the bug it guards.
    original = getattr(request.config, "_original_mra_environment", {})
    exported = original.get("MRA_LEAN_PROJECT")
    if not exported:
        pytest.skip("MRA_LEAN_PROJECT was not set in this shell")

    assert os.environ.get("MRA_LEAN_PROJECT") == exported, (
        "the harness cleared the project path the operator exported; "
        "tests/test_lean_real.py cannot run"
    )
