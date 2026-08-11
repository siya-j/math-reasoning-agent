"""Offline tests for prover selection and instrumentation.

Two properties matter here:

  * the baseline is reachable and UNCHANGED — `pipeline/prover.py` has no
    knowledge that an alternative exists
  * both provers are counted the same way, so a success rate can be weighed
    against what it cost
"""

import config
from domain.proof import Telemetry
from domain.verdict import Verdict, VerificationStatus as S
from pipeline import proving

ACCEPTED = Verdict(S.TRUE, "lean", "accepted")
REJECTED = Verdict(S.UNKNOWN, "lean", "error: no")


class Formalizer:
    _skeleton = "by attempt"

    def statement(self, goal):
        return "theorem t : True"

    def sketch(self, goal):
        return "sketch"

    def proof(self, statement, sketch, errors="", previous=""):
        return "by attempt"

    def skeleton(self, statement, sketch, count=4):
        return self._skeleton

    def lemmas(self, goal, count):
        return []

    def synthesis(self, statement, lemmas):
        return "by attempt"

    def premises_for(self, statement):
        return []


def accepts(statement, proof):
    return ACCEPTED


def rejects(statement, proof):
    return REJECTED


# ----------------------------------------------------------------- selection
def test_the_baseline_is_the_default():
    assert config.PROVER == proving.PIPELINE


def test_the_baseline_prover_runs_when_selected(monkeypatch):
    monkeypatch.setattr(config, "PROVER", proving.PIPELINE)
    run = proving.prove(
        "a claim", formalizer=Formalizer(), check=accepts, depth=0
    )
    assert run.proved


def test_the_agentic_prover_runs_when_selected(monkeypatch):
    monkeypatch.setattr(config, "PROVER", proving.AGENTIC)

    def factory(model, tools, system_prompt):
        by_name = {tool.__name__: tool for tool in tools}

        class Agent:
            def invoke(self, payload):
                by_name["try_proof"]("exact foo")
                return {"messages": []}

        return Agent()

    run = proving.prove(
        "a claim", formalizer=Formalizer(), check=accepts,
        search=None, agent_factory=factory,
    )
    assert run.proved
    assert run.proof == "exact foo"


# ------------------------------------------------------------ instrumentation
def test_the_baseline_is_instrumented_without_being_modified(monkeypatch):
    """`prover.prove` accepts its formalizer and check as arguments, so its
    cost is counted by wrapping what goes IN. Nothing inside it changes."""
    monkeypatch.setattr(config, "PROVER", proving.PIPELINE)

    run = proving.prove(
        "a claim", formalizer=Formalizer(), check=rejects, depth=0
    )

    assert run.telemetry.model_calls > 0, "model calls were not counted"
    assert run.telemetry.lean_calls > 0, "lean calls were not counted"
    assert run.telemetry.seconds >= 0


def test_retrieval_is_not_counted_as_a_model_call():
    """Counting them together would make the two provers incomparable."""
    telemetry = Telemetry()
    wrapped = proving.CountingFormalizer(Formalizer(), telemetry)

    wrapped.statement("goal")
    wrapped.premises_for("theorem t : True")

    assert telemetry.model_calls == 1
    assert telemetry.retrieval_calls == 1


def test_the_wrapper_passes_through_optional_capabilities():
    """The baseline probes for `skeleton` with getattr; hiding it would
    silently disable a whole stage."""
    wrapped = proving.CountingFormalizer(Formalizer(), Telemetry())

    assert getattr(wrapped, "skeleton", None) is not None
    assert getattr(wrapped, "does_not_exist", None) is None


def test_telemetry_reads_as_one_line():
    telemetry = Telemetry(model_calls=7, lean_calls=6, retrieval_calls=4)
    assert "7 model" in telemetry.summary()
    assert "6 lean" in telemetry.summary()
