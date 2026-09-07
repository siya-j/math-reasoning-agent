"""The two audit findings that would have spoiled a hundred-problem run."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import evaluate_proofs as cli  # noqa: E402
from domain.proof import ProofRun, Telemetry, Verdict, VerificationStatus  # noqa: E402
from eval.proof_metrics import ProofOutcome, classify, summarize  # noqa: E402


def crashed_run():
    """What `harness.prove` leaves behind when the agent raises: the record of
    whatever the tools did, plus a trace line, and no transcript."""
    run = ProofRun(goal="g")
    run.statement = "theorem t : True"
    run.verdict = Verdict(VerificationStatus.UNKNOWN, "prover", "")
    run.telemetry = Telemetry(complete=False)
    run.trace = ["agent failed: RateLimitError: 429"]
    return run


# =====================================================================
# FINDING 1 — a crash was scored as a proving failure
# =====================================================================
def test_a_crashed_goal_is_an_error_not_a_proving_failure():
    """MEASURED on eval/results/putnam-run3.json: two goals recorded
    "agent failed: " and were scored NOT_PROVED, landing in the proof-rate
    denominator. One was `putnam_1962_b1`, which run2 PROVED and run4 proved
    again -- a transient crash manufactured a failure for a goal this agent
    can do. Corrected, that run reads 33% over 3 goals rather than 20% over 5.
    """
    assert classify(crashed_run()) is ProofOutcome.ERROR


def test_a_crashed_goal_is_excluded_from_every_rate():
    """ERROR is the outcome that does not count, which is the whole point: a
    run that died is evidence about nothing."""
    summary = summarize([cli.result_from(
        __import__("eval.proof_dataset", fromlist=["Goal"]).Goal(
            id="g", goal="g", area="a",
            tier=__import__("eval.proof_dataset", fromlist=["Tier"]).Tier.PUTNAM),
        crashed_run())])

    assert summary["errors"] == 1
    assert summary["attempted"] == 0
    assert summary["proof_rate"] is None, "a dead run must not produce a rate"


def test_a_proof_survives_a_later_crash():
    """Ordering: if the compiler accepted a proof before the agent died, it is
    still proved. The crash check must not outrank the compiler."""
    run = crashed_run()
    run.proof = "by trivial"
    run.verdict = Verdict(VerificationStatus.TRUE, "lean", "")

    assert classify(run) is ProofOutcome.PROVED


def test_a_timeout_is_still_exhausted_not_an_error():
    """The two failure paths are distinct: running out of clock means the
    agent RAN and lost, which belongs in EXHAUSTED. Only a crash is ERROR."""
    run = crashed_run()
    run.trace = ["stopped early: wall clock spent (3600s)"]

    assert classify(run) is ProofOutcome.EXHAUSTED


# =====================================================================
# FINDING 2 — the record could not attribute its own numbers
# =====================================================================
def test_the_run_records_what_produced_it():
    """`environment()` records the Lean backend, and its docstring says why:
    "A benchmark number that cannot be attributed to a backend is not a
    measurement." That was only half applied -- the model, budget, goals file
    and commit were printed at startup and lost. A results file said 40% and
    could not say 40% of what."""
    class Args:
        goals = "eval/putnam.json"
        budget_profile = "hard-reasoning"
        limit = 100
        tier = None
        goal = None

    info = cli.invocation(Args(), {"MRA_MAX_AGENT_LEAN": "40"})

    assert info["model"], "the model is not recorded"
    assert info["prover"], "the prover is not recorded"
    assert info["goals_file"] == "eval/putnam.json"
    assert info["budget_profile"] == "hard-reasoning"
    assert info["budget"] == {"MRA_MAX_AGENT_LEAN": "40"}, (
        "the profile's VALUES matter, not just its name")
    assert info["limit"] == 100
    assert info["started"]


def test_the_commit_is_recorded_so_the_code_version_is_known():
    class Args:
        goals = ""
        budget_profile = None
        limit = None
        tier = None
        goal = None

    info = cli.invocation(Args(), {})

    assert len(info["commit"]) == 40, f"not a commit sha: {info['commit']!r}"


def test_a_missing_git_does_not_fail_the_run(monkeypatch):
    """A benchmark must not die because git is absent."""
    def explode(*a, **k):
        raise OSError("no git here")

    monkeypatch.setattr(cli.subprocess, "run", explode)

    class Args:
        goals = ""
        budget_profile = None
        limit = None
        tier = None
        goal = None

    assert cli.invocation(Args(), {})["commit"] == ""


def test_the_saved_file_carries_the_run_block(tmp_path):
    out = tmp_path / "r.json"
    cli.save([], {"total": 0}, out, {"model": "m", "commit": "abc"})

    written = json.loads(out.read_text(encoding="utf-8"))

    assert written["run"]["model"] == "m"
    assert written["run"]["commit"] == "abc"
    assert "environment" in written and "summary" in written
