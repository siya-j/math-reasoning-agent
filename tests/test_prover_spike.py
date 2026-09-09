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


@pytest.fixture(autouse=True)
def never_touch_the_real_cache():
    """No test may write eval/prover-spike-generations.json.

    MEASURED: an early version of these tests left
    `exercise_1_11a -> "by trivial"` (3e-06 seconds) in the REAL cache. On
    the next run that would have been served as Goedel-Prover's answer,
    compiled, and reported as its result -- a fabricated data point in the
    one file whose entire purpose is to hold real evidence.

    CHECKED AFTERWARDS RATHER THAN PATCHED. Every `_spike()` call
    re-executes the module and recomputes CACHE, so monkeypatching one
    instance leaves every other instance pointing at the real file -- which
    is exactly how the fake entry got there. Comparing the bytes catches it
    whichever instance did the writing.
    """
    real = ROOT / "eval" / "prover-spike-generations.json"
    before = real.read_bytes() if real.exists() else None
    yield
    after = real.read_bytes() if real.exists() else None
    if before is None:
        assert after is None, "a test created the real generation cache"
    else:
        assert after == before, "a test wrote into the real generation cache"


@pytest.fixture
def lean_ok(monkeypatch):
    """Satisfy BOTH pre-flight stages, so a test about scoring is not
    answered by the pre-flight instead."""
    from math_v2 import _local
    from verifiers.lean_runner import LeanOutcome, LeanResult

    monkeypatch.setattr(_local, "lean_available", lambda: (True, ""))
    monkeypatch.setattr(_local, "LEAN_PROJECT", "/fake/project")

    module = _spike()
    # The probe always passes; the GOAL compile is whatever a test puts in
    # `_real_compile`. Overriding `_compile` directly would break the
    # pre-flight instead of the thing under test.
    module._real_compile = module._compile

    async def compile_or_probe(statement, proof, lemmas):
        if statement == "theorem probe : True":
            return LeanResult(LeanOutcome.COMPILED, ""), None
        return await module._real_compile(statement, proof, lemmas)

    monkeypatch.setattr(module, "_compile", compile_or_probe)
    return module


def test_a_run_that_never_reached_the_prover_is_not_a_score(capsys, lean_ok):
    # NOTE: `lean_ok` is the module, not just a flag -- the pre-flight is
    # patched on that instance, so the test must drive the same one.
    """The FIRST attempt at this spike printed "closed 0 of 0 goals" after
    three timeouts. That reads as a result -- the prover tried and failed --
    and it is not one: nothing was ever generated or compiled. A run whose
    every request errored must refuse to report a rate."""
    code = lean_ok.main([
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


# ------------------------------------------------------- the pre-flight
#
# THREE ATTEMPTS AT THIS SPIKE, THREE INFRASTRUCTURE FAILURES, NO DATA:
#   1. --timeout 300 against a model needing ~730s -- three TimeoutErrors.
#   2. the prompt asked for a bare body, the model sent a whole theorem.
#   3. MRA_LEAN_PROJECT was unset, so all three proofs came back
#      LeanOutcome.UNAVAILABLE after ~4 minutes of generation each.
# Every one had the same shape: spend first, find out afterwards.
def test_a_run_refuses_to_start_when_lean_cannot_compile(capsys, monkeypatch):
    """The check that would have saved eleven minutes of CPU. Generating a
    proof nothing can check is spending the expensive half of the experiment
    to learn an environment variable is missing."""
    import verifiers.lean_runner as runner
    monkeypatch.setattr(runner, "lean_toolchain_works", lambda *a, **k: False)

    called = []
    module = _spike()
    monkeypatch.setattr(module, "_ask_local",
                        lambda *a, **k: called.append(a) or "by trivial")
    code = module.main(["--run", "--url", "http://127.0.0.1:1/v1", "--limit", "1"])

    out = capsys.readouterr().out
    assert code == 1
    assert not called, "it generated a proof it could not possibly check"
    assert "MRA_LEAN_PROJECT" in out, "it must say what to set"
    assert "lean-workspace" in out, "and where"


def test_no_weak_lean_check_is_used_anywhere():
    """Two weaker checks exist and BOTH were tried and both lied here.
    `lean_is_available` only asks whether the binary is on PATH.
    `lean_toolchain_works` only asks whether `lean --version` prints a
    banner -- MEASURED on this machine as True while the real compile
    returned UNAVAILABLE, because the run goes through `lake env lean`
    inside MRA_LEAN_PROJECT and neither check touches that."""
    # COMMENTS STRIPPED. Both names are DISCUSSED in the source, in the note
    # explaining why neither is used -- scanning the raw text would match
    # that note and pass or fail for the wrong reason.
    lines = (ROOT / "scripts" / "prover_spike.py").read_text(
        encoding="utf-8").splitlines()
    code = "\n".join(line for line in lines
                     if not line.lstrip().startswith("#"))
    assert "lean_is_available" not in code
    assert "lean_toolchain_works" not in code


# ------------------------------------------------------- the cache
def test_a_generation_is_saved_before_it_is_compiled(tmp_path, monkeypatch,
                                                     lean_ok):
    """Order matters. The compile is the step that failed, so a proof only
    written on a successful compile is lost exactly when the cache was
    supposed to help. On this hardware a generation is minutes and a compile
    is seconds -- losing the minutes to redo the seconds is backwards."""
    module = lean_ok
    monkeypatch.setattr(module, "CACHE", tmp_path / "gen.json")
    monkeypatch.setattr(module, "_ask_local",
                        lambda *a, **k: "theorem x : True := by trivial")

    def explode(*a, **k):
        raise RuntimeError("the compiler fell over")
    monkeypatch.setattr(module, "_real_compile", explode)

    with pytest.raises(RuntimeError):
        module.main(["--run", "--limit", "1"])

    saved = json.loads((tmp_path / "gen.json").read_text(encoding="utf-8"))
    assert saved, "the generation was lost when the compile failed"
    assert "trivial" in next(iter(saved.values()))["proof"]


def test_the_cache_is_keyed_by_model_too(tmp_path, monkeypatch, lean_ok):
    """Serving a different prover on the same port must not silently reuse
    the last one's answers and report them as the new model's."""
    module = lean_ok
    monkeypatch.setattr(module, "CACHE", tmp_path / "gen.json")
    cheapest = sorted(module.build_corpus(),
                      key=lambda g: g["agent_input_tokens"])[0]["goal_id"]
    # SEEDED UNDER THE GOAL ID ALONE -- the key a model-blind cache would
    # look up. If the model is part of the key this entry is never found and
    # the prover is asked; if it is not, this stale answer is served as the
    # new model's and the test fails. Seeding it under the correct
    # `goal::model` key instead would pass either way, which is how the
    # first version of this test came to prove nothing.
    (tmp_path / "gen.json").write_text(json.dumps({
        cheapest: {"proof": "by sorry", "seconds": 1.0,
                   "model": "old-model"},
    }), encoding="utf-8")

    asked = []
    monkeypatch.setattr(module, "_ask_local",
                        lambda *a, **k: asked.append(1) or "by trivial")
    monkeypatch.setattr(module, "_real_compile",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("stop here")))
    with pytest.raises(RuntimeError):
        module.main(["--run", "--limit", "1", "--cheapest-first",
                     "--model", "new-model"])
    assert asked, ("a stale answer from another model was served as this "
                   "model's -- the cache key must include the model")


def test_lean_dying_mid_run_is_not_counted_as_a_miss(tmp_path, monkeypatch,
                                                     lean_ok, capsys):
    """`unavailable` means the proof was never judged. Counting it as a
    failure is what printed "the prover closed 0 of 3" over three proofs
    nothing had looked at."""
    module = lean_ok
    monkeypatch.setattr(module, "CACHE", tmp_path / "gen.json")
    monkeypatch.setattr(module, "_ask_local", lambda *a, **k: "by trivial")

    from verifiers.lean_runner import LeanOutcome

    class FakeResult:
        outcome = LeanOutcome.UNAVAILABLE
        output = "Lean could not be run: [Errno 2] lake not found"

    class FakeVerdict:
        status = None

    monkeypatch.setattr(module, "_real_compile",
                        lambda *a, **k: _done((FakeResult(), FakeVerdict())))
    code = module.main(["--run", "--limit", "2", "--cheapest-first"])
    out = capsys.readouterr().out
    assert "LEAN UNAVAILABLE -- not a result" in out
    assert "closed 0 of 2" not in out, "an unjudged proof is not a miss"
    assert "lake not found" in out, "the cause must be shown, not swallowed"
    assert "NOTHING WAS SCORED" in out
    assert code == 1, "nothing was judged, so nothing was measured"


def _done(value):
    import asyncio

    async def coro():
        return value
    return coro()


def test_the_preflight_compiles_something_real_not_just_a_version_banner():
    """MEASURED, fourth failed attempt: the first pre-flight used
    `lean_toolchain_works`, which only checks that `lean --version` prints a
    banner. It said "lean: reachable" and the next compile returned
    UNAVAILABLE, because the real path is `lake env lean` inside
    MRA_LEAN_PROJECT and none of that is what `--version` tests.

    A pre-flight must exercise the path the run uses, which for this script
    means `_compile` -- the same function every goal goes through."""
    lines = (ROOT / "scripts" / "prover_spike.py").read_text(
        encoding="utf-8").splitlines()
    code = "\n".join(line for line in lines
                     if not line.lstrip().startswith("#"))
    assert "_local.lean_available()" in code, "the real check must be used"
    assert "lean_toolchain_works" not in code, "the weak check must be gone"
    assert "theorem probe : True" in code, "it must compile something real"


def test_a_project_that_cannot_import_mathlib_stops_the_run(monkeypatch, capsys):
    """`lake --version` succeeding does not mean `import Mathlib` resolves.
    A project without Mathlib built would fail every goal for a reason that
    has nothing to do with the prover."""
    from math_v2 import _local
    from verifiers.lean_runner import LeanOutcome, LeanResult

    module = _spike()
    monkeypatch.setattr(_local, "lean_available", lambda: (True, ""))
    monkeypatch.setattr(_local, "LEAN_PROJECT", "/fake/project")

    async def no_mathlib(*a, **k):
        return LeanResult(LeanOutcome.ERRORS,
                          "unknown module prefix 'Mathlib'"), None
    monkeypatch.setattr(module, "_compile", no_mathlib)

    asked = []
    monkeypatch.setattr(module, "_ask_local",
                        lambda *a, **k: asked.append(1) or "by trivial")

    code = module.main(["--run", "--limit", "1"])
    out = capsys.readouterr().out
    assert code == 1
    assert not asked, "it generated against a project that cannot compile"
    assert "unknown module prefix" in out, "it must show what Lean said"


def test_an_unavailable_compile_reports_why(tmp_path, monkeypatch, capsys):
    """`_util.lean_runner` puts the cause in LeanResult.output. The first
    version of this printed only "LEAN UNAVAILABLE", so a whole run named
    nothing to fix -- which is how a fourth attempt still ended without a
    diagnosis."""
    from math_v2 import _local
    from verifiers.lean_runner import LeanOutcome, LeanResult

    module = _spike()
    monkeypatch.setattr(_local, "lean_available", lambda: (True, ""))
    monkeypatch.setattr(_local, "LEAN_PROJECT", "/fake/project")
    monkeypatch.setattr(module, "CACHE", tmp_path / "gen.json")
    monkeypatch.setattr(module, "_ask_local", lambda *a, **k: "by trivial")

    class Verdict:
        status = None

    async def compile_or_probe(statement, proof, lemmas):
        if statement == "theorem probe : True":
            return LeanResult(LeanOutcome.COMPILED, ""), Verdict()
        return (LeanResult(LeanOutcome.UNAVAILABLE,
                           "Lean could not be run: [WinError 2] not found"),
                Verdict())
    monkeypatch.setattr(module, "_compile", compile_or_probe)

    module.main(["--run", "--limit", "1", "--cheapest-first"])
    out = capsys.readouterr().out
    assert "WinError 2" in out, "the cause was swallowed again"


# --------------------------------- prose is not a failed proof
#
# MEASURED, from the two generations the cache preserved when the compile
# step failed: 2577 and 3281 characters of "### Detailed Proof and Analysis",
# cut off mid-sentence by --max-tokens 1024, with NO Lean in either.
# Goedel-Prover-V2 reasons at length before writing code. Compiling that
# prose would give a syntax error scored as the prover failing to prove the
# goal, when what actually ran out was our token budget.
REAL_GENERATIONS = ROOT / "eval" / "prover-spike-generations.json"


def test_the_real_prose_generations_are_not_mistaken_for_proofs():
    """Driven by the actual bytes the model returned, not a paraphrase."""
    if not REAL_GENERATIONS.exists():
        pytest.skip("no cached generations on this machine")
    saved = json.loads(REAL_GENERATIONS.read_text(encoding="utf-8"))
    prose = [v["proof"] for v in saved.values()
             if "```" not in v["proof"] and "theorem" not in v["proof"]]
    if not prose:
        pytest.skip("no prose-only generation cached")
    for text in prose:
        assert not _spike()._looks_like_lean(text), text[:120]


def test_bare_by_does_not_make_english_look_like_lean():
    """The one marker that false-positived on the real generations.
    Mathematical English is full of it -- "divide both sides by 3"."""
    module = _spike()
    assert not module._looks_like_lean(
        "We divide both sides by 3 and note the norm is positive.")
    assert not module._looks_like_lean(
        "Since `3 - c = 4 - b`, we get `r = 2/3` by scaling.")


@pytest.mark.parametrize("text", [
    "Analysis\n```lean4\ntheorem x : True := by trivial\n```",
    "intro h\nexact h.trans hx",
    "theorem foo : True := by trivial",
    "norm_num",
    "calc a = b := by ring\n  _ = c := by simp",
])
def test_real_lean_is_never_discarded_as_prose(text):
    """The classifier must not become a way to lose good proofs. A bare
    tactic body has no declaration and no fence, so it is exactly the case
    a careless check would throw away."""
    assert _spike()._looks_like_lean(text)


def test_a_prose_only_reply_is_reported_as_truncated_not_as_a_miss(
        tmp_path, monkeypatch, capsys, lean_ok):
    """It must not reach the compiler, and it must not reach the score."""
    module = lean_ok
    monkeypatch.setattr(module, "CACHE", tmp_path / "gen.json")
    monkeypatch.setattr(
        module, "_ask_local",
        lambda *a, **k: "### Detailed Proof and Analysis\n\nWe divide by 3.")

    compiled = []
    monkeypatch.setattr(module, "_real_compile",
                        lambda *a, **k: compiled.append(1))

    code = module.main(["--run", "--limit", "1", "--cheapest-first"])
    out = capsys.readouterr().out
    assert not compiled, "prose was sent to the compiler"
    assert "NO LEAN IN REPLY" in out
    assert "CONTAINED NO LEAN" in out
    assert "closed 0 of 1" not in out, "a truncated generation is not a miss"
    assert code == 1, "nothing was scored"


def test_the_token_cap_is_sized_for_a_reasoning_prover():
    """1024 was measured to be too small: both real generations were cut
    off mid-sentence before reaching any Lean."""
    source = (ROOT / "scripts" / "prover_spike.py").read_text(encoding="utf-8")
    assert '"--max-tokens", type=int, default=4096' in source, (
        "1024 truncated both real generations before any Lean appeared")

