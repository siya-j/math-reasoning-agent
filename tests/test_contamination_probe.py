"""The recall probe's comparison must be measuring the right thing.

AN EARLIER VERSION OF THIS SCRIPT MEASURED NOTHING. It compared the agent's
accepted PROOF against ProofNet's reference text by 5-gram overlap -- but
every ProofNet entry ships its statement with the proof replaced by `sorry`
(0 of 182 notes contain `:= by`), so there was no reference proof to compare
against and the number was just "how much of the signature does the proof
happen to restate". These tests exist because that mistake was shipped and
caught, and the fix rests on two small text operations that are easy to get
wrong silently.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _probe():
    spec = importlib.util.spec_from_file_location(
        "contamination", ROOT / "scripts" / "contamination.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOTE = """open Topology Filter Real Complex
open scoped BigOperators

theorem exercise_1_18a
  (n : ℕ)
  (h : n > 1)
  (x : EuclideanSpace ℝ (Fin n))
  : ∃ (y : EuclideanSpace ℝ (Fin n)), y ≠ 0 ∧ inner x y = 0:= sorry"""


def test_the_reference_drops_the_sorry():
    """`:= sorry` is in every entry. Leaving it in gives every answer free
    similarity, since the probe asks for a signature and no model would
    volunteer `sorry`."""
    ref = _probe().reference(NOTE)
    assert "sorry" not in ref, ref


def test_the_reference_drops_the_open_preamble():
    """The `open ...` lines are boilerplate shared by every goal in a chapter.
    Left in, they would give each answer a floor of similarity for free and
    compress the very differences the probe is looking for."""
    ref = _probe().reference(NOTE)
    assert "open Topology" not in ref, ref
    assert "open scoped" not in ref, ref
    # ...and the theorem itself survives.
    assert "theorem exercise_1_18a" in ref
    assert "EuclideanSpace" in ref


def test_similarity_is_order_insensitive_but_not_blind():
    probe = _probe()
    ref = probe.reference(NOTE)
    assert probe.jaccard(ref, ref) == 1.0
    assert probe.jaccard(ref, "theorem unrelated : 2 + 2 = 4") < 0.2
    # Whitespace is not a signal: reflowing must not change the score much.
    reflowed = " ".join(ref.split())
    assert probe.jaccard(ref, reflowed) > 0.95


def test_a_declined_answer_scores_zero_not_a_coincidence():
    """The prompt offers `UNKNOWN` deliberately, so a model that has not seen
    a theorem can say so rather than inventing a plausible-looking statement
    that would score as partial recall."""
    probe = _probe()
    assert "UNKNOWN" in probe.ASK
    assert probe.jaccard("UNKNOWN", probe.reference(NOTE)) < 0.1


def test_no_reference_proofs_exist_which_is_why_the_design_changed():
    """The finding that invalidated the first version, asserted so nobody
    rebuilds it: ProofNet ships statements, not proofs."""
    path = ROOT / "eval" / "proofnet-182.json"
    if not path.exists():
        pytest.skip("proofnet-182.json is not present")
    goals = json.loads(path.read_text(encoding="utf-8"))
    with_proof = [g["id"] for g in goals if ":= by" in (g.get("note") or "")]
    assert not with_proof, (
        f"reference PROOFS now exist for {len(with_proof)} goals, so a "
        f"proof-similarity check is possible after all and this script's "
        f"design should be revisited"
    )


def test_the_dry_run_is_the_default_and_spends_nothing(capsys):
    """`--run` is opt-in because this script is the only analysis tool here
    that costs money."""
    probe = _probe()
    code = probe.main(["--goals", "eval/proofnet-182.json", "--limit", "2"])
    assert code == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Nothing is spent" in out


# ------------------------------------------------- the null baseline
#
# WHY IT DECIDES WHETHER THE PROBE MEANS ANYTHING. A Jaccard of 0.35 against
# the reference is uninterpretable alone: Lean statements about the same
# material share `theorem exercise_`, the binders, the arrows, the typeclass
# names. A model that had never seen ProofNet and simply wrote a competent
# formalisation would still score well above zero. So each answer is also
# scored against every OTHER goal's reference -- pairings wrong by
# construction, whose distribution is the score shared notation alone buys.
# No extra API calls: the same answers, re-compared.
def _fake_run(monkeypatch, tmp_path, answers, capsys, extra=()):
    """Drive `--run` with a scripted model. Spends nothing."""
    module = _probe()

    goals = [
        {"id": "exercise_1_1", "area": "proofnet 1",
         "note": "theorem exercise_1_1 (x : ℝ) : x + 0 = x := sorry"},
        {"id": "exercise_1_2", "area": "proofnet 1",
         "note": "theorem exercise_1_2 (y : ℝ) : y * 1 = y := sorry"},
        {"id": "exercise_2_1", "area": "proofnet 2",
         "note": "theorem exercise_2_1 (G : Type*) [Group G] : True := sorry"},
    ]
    goals_file = tmp_path / "goals.json"
    goals_file.write_text(json.dumps(goals), encoding="utf-8")

    replies = iter(answers)

    class FakeModel:
        def invoke(self, _prompt):
            class Reply:
                content = next(replies)
            return Reply()

    import llm
    monkeypatch.setattr(llm, "get_model", lambda *a, **k: FakeModel())
    code = module.main(["--goals", str(goals_file), "--run", *extra])
    return code, capsys.readouterr().out


def test_a_perfect_recall_shows_a_large_lift(monkeypatch, tmp_path, capsys):
    """Answers identical to their own references, so the matched score is 1.0
    and any wrong pairing is far lower. That gap is the signal."""
    _, out = _fake_run(monkeypatch, tmp_path, [
        "theorem exercise_1_1 (x : ℝ) : x + 0 = x",
        "theorem exercise_1_2 (y : ℝ) : y * 1 = y",
        "theorem exercise_2_1 (G : Type*) [Group G] : True",
    ], capsys)
    assert "NULL BASELINE" in out
    assert "reproducing THESE formalisations specifically" in out


def test_notation_alone_shows_no_lift(monkeypatch, tmp_path, capsys):
    """THE CASE THAT MATTERS. Every answer is the same generic statement, so
    it matches its own reference no better than anyone else's. The raw score
    is non-zero -- shared notation -- and the probe must say the signal is
    not there rather than report the raw number as recall."""
    generic = "theorem exercise_0_0 (x : ℝ) : x = x"
    _, out = _fake_run(monkeypatch, tmp_path, [generic] * 3, capsys)
    assert "NULL BASELINE" in out
    assert "no recall signal here at all" in out


def test_the_control_costs_no_extra_model_calls(monkeypatch, tmp_path, capsys):
    """One completion per goal, and the baseline is computed from those same
    answers. If it ever started asking again, the probe's cost would silently
    multiply by the number of goals."""
    module = _probe()
    calls = []

    goals = [{"id": f"exercise_1_{i}", "area": "proofnet 1",
              "note": f"theorem exercise_1_{i} : True := sorry"}
             for i in range(4)]
    goals_file = tmp_path / "goals.json"
    goals_file.write_text(json.dumps(goals), encoding="utf-8")

    class FakeModel:
        def invoke(self, prompt):
            calls.append(prompt)

            class Reply:
                content = "theorem whatever : True"
            return Reply()

    import llm
    monkeypatch.setattr(llm, "get_model", lambda *a, **k: FakeModel())
    module.main(["--goals", str(goals_file), "--run"])
    assert len(calls) == 4, f"{len(calls)} calls for 4 goals"


def test_decided_only_needs_results_and_says_so(capsys, tmp_path):
    module = _probe()
    goals_file = tmp_path / "g.json"
    goals_file.write_text(json.dumps(
        [{"id": "exercise_1_1", "area": "a", "note": "theorem x : True := sorry"}]),
        encoding="utf-8")
    code = module.main(["--goals", str(goals_file), "--decided-only"])
    assert code == 2
    assert "needs --results" in capsys.readouterr().out


def test_decided_only_narrows_to_goals_with_an_outcome(tmp_path, capsys):
    """The proved-versus-not comparison is the answerable question, so an
    undecided goal is spend with no return."""
    module = _probe()
    goals = [{"id": f"exercise_1_{i}", "area": "proofnet 1",
              "note": f"theorem exercise_1_{i} : True := sorry"}
             for i in range(5)]
    goals_file = tmp_path / "g.json"
    goals_file.write_text(json.dumps(goals), encoding="utf-8")
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"results": [
        {"goal_id": "exercise_1_0", "tier": "proofnet", "outcome": "proved"},
        {"goal_id": "exercise_1_3", "tier": "proofnet", "outcome": "exhausted"},
    ]}), encoding="utf-8")

    module.main(["--goals", str(goals_file), "--results", str(results),
                 "--decided-only"])
    out = capsys.readouterr().out
    assert "2 goals would be probed" in out, out[:300]


# ------------------------------------------- the shell that does not glob
#
# MEASURED, on the user's machine, with a command I gave them:
#   --results eval\results\*.json
# works in bash and does NOT in PowerShell, which passes the pattern through
# literally. The script read a file named `*.json`, found nothing, and
# printed "no goals in this file have a recorded outcome" -- a claim about
# the DATA for a fault in the ARGUMENTS. `outcomes` catching OSError is what
# made the two indistinguishable.
def test_a_glob_the_shell_did_not_expand_is_expanded_here(tmp_path):
    """The same command has to work in both shells."""
    module = _probe()
    results = tmp_path / "results"
    results.mkdir()
    for name in ("a.json", "b.json"):
        (results / name).write_text(json.dumps({"results": [
            {"goal_id": f"exercise_{name[0]}", "tier": "proofnet",
             "outcome": "proved"},
        ]}), encoding="utf-8")

    found = module.outcomes([str(results / "*.json")])
    assert set(found) == {"exercise_a", "exercise_b"}, found


def test_a_path_matching_nothing_is_reported_not_swallowed(capsys):
    """Silence here is what turned an argument mistake into a false
    statement about the goals."""
    module = _probe()
    assert module.outcomes(["/definitely/not/here/*.json"]) == {}
    assert "matched no file" in capsys.readouterr().out


def test_an_explicit_path_still_works(tmp_path):
    """Expanding must not break the ordinary case of naming one file."""
    module = _probe()
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"results": [
        {"goal_id": "exercise_1_1", "tier": "proofnet", "outcome": "proved"},
    ]}), encoding="utf-8")
    assert module.outcomes([str(path)]) == {"exercise_1_1": "proved"}


def test_no_matching_results_blames_the_arguments_not_the_data(tmp_path,
                                                               capsys):
    """The message must point at the path, because that is what was wrong."""
    module = _probe()
    goals = tmp_path / "g.json"
    goals.write_text(json.dumps([
        {"id": "exercise_1_1", "area": "a", "note": "theorem x : True := sorry"},
    ]), encoding="utf-8")
    code = module.main(["--goals", str(goals), "--decided-only",
                        "--results", str(tmp_path / "missing-*.json")])
    out = capsys.readouterr().out
    assert code == 2
    assert "matched no file" in out
    assert "PowerShell" in out, "it must name the shell that does this"


def test_results_for_a_different_goal_set_says_so(tmp_path, capsys):
    """Distinct from 'no results at all' -- outcomes exist, they are just
    for other goals, and conflating the two hides a real mismatch."""
    module = _probe()
    goals = tmp_path / "g.json"
    goals.write_text(json.dumps([
        {"id": "exercise_9_9", "area": "a", "note": "theorem x : True := sorry"},
    ]), encoding="utf-8")
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"results": [
        {"goal_id": "exercise_1_1", "tier": "proofnet", "outcome": "proved"},
    ]}), encoding="utf-8")
    code = module.main(["--goals", str(goals), "--decided-only",
                        "--results", str(results)])
    out = capsys.readouterr().out
    assert code == 2
    assert "different sets" in out


# ------------------------------------------- exhaustion, said in plain words
#
# THE REAL ERROR, verbatim from the run that hit it:
#   ChatGoogleGenerativeAIError: Error calling model 'gemini-3.5-flash'
#   (RESOURCE_EXHAUSTED): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429,
#   'message': 'Your project has exceeded its monthly spending cap. ...
#
# 429 means BOTH "slow down" and "you are out of money", so only the message
# separates them -- and the first version of this handler printed the type
# alone, discarding it.
CAP = (
    "Error calling model 'gemini-3.5-flash' (RESOURCE_EXHAUSTED): 429 "
    "RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'Your project "
    "has exceeded its monthly spending cap. Please go to AI Studio at "
    "https://ai.studio/spend to manage your project spend cap.', 'status': "
    "'RESOURCE_EXHAUSTED'}}"
)


def _one_goal(tmp_path):
    goals = tmp_path / "g.json"
    goals.write_text(json.dumps([
        {"id": f"exercise_1_{i}", "area": "proofnet 1",
         "note": f"theorem exercise_1_{i} : True := sorry"} for i in range(9)
    ]), encoding="utf-8")
    return goals


def test_a_spending_cap_stops_on_the_very_first_goal(tmp_path, monkeypatch,
                                                     capsys):
    """Not after three. A cap refuses every remaining goal, so more attempts
    buy nothing but more of the same line -- and this probe was about to
    print it 48 times."""
    module = _probe()
    calls = []

    class FakeModel:
        def invoke(self, _prompt):
            calls.append(1)
            raise RuntimeError(CAP)

    import llm
    monkeypatch.setattr(llm, "get_model", lambda *a, **k: FakeModel())
    code = module.main(["--goals", str(_one_goal(tmp_path)), "--run"])
    out = capsys.readouterr().out

    assert code == 1
    assert len(calls) == 1, f"it kept going for {len(calls)} goals"
    assert "SPENDING CAP" in out, "it must name which wall was hit"
    assert "ai.studio/spend" in out, "and where it is changed"
    assert "not a bug" in out


def test_the_provider_message_is_not_discarded(tmp_path, monkeypatch, capsys):
    """`FAILED ChatGoogleGenerativeAIError` alone gave nothing to act on."""
    module = _probe()

    class FakeModel:
        def invoke(self, _prompt):
            raise RuntimeError(CAP)

    import llm
    monkeypatch.setattr(llm, "get_model", lambda *a, **k: FakeModel())
    module.main(["--goals", str(_one_goal(tmp_path)), "--run"])
    out = capsys.readouterr().out
    assert "monthly spending cap" in out, "the message itself must appear"


def test_a_transient_failure_does_not_stop_the_sweep(tmp_path, monkeypatch,
                                                     capsys):
    """The distinction must hold in BOTH directions. Giving up on a dropped
    connection would waste a run the same way retrying a cap wastes time."""
    module = _probe()
    calls = []

    class FakeModel:
        def invoke(self, _prompt):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("ReadError: connection reset by peer")

            class Reply:
                content = "theorem exercise_1_1 : True"
            return Reply()

    import llm
    monkeypatch.setattr(llm, "get_model", lambda *a, **k: FakeModel())
    module.main(["--goals", str(_one_goal(tmp_path)), "--run"])
    assert len(calls) == 9, f"stopped after {len(calls)} on a transient fault"

