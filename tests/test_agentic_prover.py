"""Offline tests for the agentic prover. No model, no Lean, no network.

The agent is replaced by a scripted one that CALLS THE REAL TOOLS, so what
is exercised is the contract between the agent and the log — which is where
the guarantee lives.

The load-bearing test is `test_prose_alone_never_establishes_a_proof`. An
agent that says it is finished must not be believed.
"""

import pytest

import config
from domain.proof import ProofStage
from domain.verdict import Verdict, VerificationStatus as S
from pipeline.agentic_prover import prove
from pipeline.proof_tools import ProofLog, make_proof_tools
from retrieval.loogle import Premise

ACCEPTED = Verdict(S.TRUE, "lean", "Lean accepted a complete proof.")
REJECTED = Verdict(
    S.UNKNOWN, "lean", "error: unsolved goals\n⊢ IsCyclic G"
)


class Formalizer:
    def __init__(self, statement="theorem t (G : Type*) : IsCyclic G"):
        self._statement = statement

    def statement(self, goal):
        return self._statement


@pytest.fixture(autouse=True)
def _no_statement_check(monkeypatch):
    """These tests are about the agent loop, not the statement pre-flight.

    Also keeps the suite honest: without this the pre-flight calls the real
    `run_lean`, so the tests would pass only on a machine with no Lean.
    """
    monkeypatch.setattr(config, "CHECK_STATEMENT", False)


class Search:
    """A fake Loogle. Records what the agent chose to look for."""

    def __init__(self, results=None):
        self.queries = []
        self._results = results if results is not None else [
            Premise(name="isCyclic_of_prime_card", type=" (h : Nat.card α = p) : IsCyclic α")
        ]

    def search(self, query, limit=None):
        return self.search_with_suggestions(query, limit)[0]

    def search_with_suggestions(self, query, limit=None):
        self.queries.append(query)
        return list(self._results), []


def scripted_agent(script):
    """An agent that performs `script` — a list of (tool_name, argument).

    It calls the REAL tools, so the log, the telemetry and the verdict are
    all produced by the same code path a live agent would use.
    """

    def factory(model, tools, system_prompt):
        by_name = {tool.__name__: tool for tool in tools}
        factory.prompt = system_prompt
        factory.tool_names = list(by_name)

        class Agent:
            def invoke(self, payload):
                for name, argument in script:
                    tool = by_name[name]
                    tool() if argument is None else tool(argument)
                return {"messages": [type("M", (), {"text": "I am finished."})()]}

        return Agent()

    return factory


def accepts(statement, proof):
    return ACCEPTED


def rejects(statement, proof):
    return REJECTED


def accepts_only(marker):
    def check(statement, proof):
        return ACCEPTED if marker in proof else REJECTED

    return check


# ------------------------------------------------------------- the guard
def test_prose_alone_never_establishes_a_proof():
    """THE constraint. The agent claims success and calls nothing."""
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,                       # the compiler would accept
        search=Search(),
        agent_factory=scripted_agent([]),    # ...but nothing was submitted
    )
    assert not run.proved
    assert run.verdict.status is S.UNKNOWN
    assert "0 compilation" in run.verdict.detail


def test_a_recorded_compilation_is_what_proves_it():
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,
        search=Search(),
        agent_factory=scripted_agent([("try_proof", "exact foo")]),
    )
    assert run.proved
    assert run.proof == "exact foo"


def test_a_rejected_attempt_does_not_prove_anything():
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=rejects,
        search=Search(),
        agent_factory=scripted_agent([("try_proof", "exact nonsense")]),
    )
    assert not run.proved
    assert len(run.attempts) == 1


def test_an_agent_crash_keeps_whatever_was_already_proved():
    """A harness failure must not discard a compilation that succeeded."""

    def factory(model, tools, system_prompt):
        by_name = {tool.__name__: tool for tool in tools}

        class Agent:
            def invoke(self, payload):
                by_name["try_proof"]("exact foo")
                raise RuntimeError("harness exploded")

        return Agent()

    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,
        search=Search(),
        agent_factory=factory,
    )
    assert run.proved, "a successful compilation was lost to a crash"


# ------------------------------------------------------- state and feedback
def test_the_agent_sees_the_goal_state_after_a_failure():
    """The whole reason for the rewrite: feedback the model can act on."""
    seen = []

    def factory(model, tools, system_prompt):
        by_name = {tool.__name__: tool for tool in tools}

        class Agent:
            def invoke(self, payload):
                seen.append(by_name["try_proof"]("exact wrong"))
                return {"messages": []}

        return Agent()

    prove("a claim", formalizer=Formalizer(), check=rejects, search=Search(),
          agent_factory=factory)

    assert "REJECTED" in seen[0]
    assert "⊢ IsCyclic G" in seen[0], "the goal state never reached the agent"


def test_the_agent_can_search_repeatedly_and_then_compile():
    """Interleaving is the point: search, learn, search again, then write."""
    search = Search()
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts_only("isCyclic_of_prime_card"),
        search=search,
        agent_factory=scripted_agent(
            [
                ("search_mathlib", "IsCyclic"),
                ("try_proof", "exact wrong_guess"),
                ("search_mathlib", "|- IsCyclic _"),
                ("try_proof", "exact isCyclic_of_prime_card h"),
            ]
        ),
    )

    assert search.queries == ["IsCyclic", "|- IsCyclic _"]
    assert run.proved
    assert len(run.attempts) == 2, "both compilations should be recorded"


def test_retrieved_premises_accumulate_across_searches():
    log = ProofLog(statement="theorem t : True")
    tools = {t.__name__: t for t in make_proof_tools(log, accepts, Search())}

    tools["search_mathlib"]("one")
    tools["search_mathlib"]("two")

    assert len(log.premises) == 1, "the same premise was recorded twice"


def test_standard_tactics_use_everything_found_so_far():
    log = ProofLog(statement="theorem t : True")
    tools = {t.__name__: t for t in make_proof_tools(log, rejects, Search())}

    tools["search_mathlib"]("IsCyclic")
    tools["try_standard_tactics"]()

    assert "isCyclic_of_prime_card" in log.attempts[0].proof


# --------------------------------------------------------- degradation
def test_search_being_unavailable_does_not_break_the_run():
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,
        search=None,
        agent_factory=scripted_agent(
            [("search_mathlib", "anything"), ("try_proof", "exact foo")]
        ),
    )
    assert run.proved


def test_a_claim_that_cannot_be_formalised_never_reaches_the_agent():
    called = []

    def factory(model, tools, system_prompt):
        called.append(True)
        raise AssertionError("the agent should not have been built")

    run = prove(
        "???", formalizer=Formalizer(statement="  "), check=accepts,
        agent_factory=factory,
    )
    assert not run.proved
    assert not called


# ------------------------------------------------------------ instrumentation
def test_every_kind_of_call_is_counted():
    """Success rate without a budget is not a comparison."""
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=rejects,
        search=Search(),
        agent_factory=scripted_agent(
            [
                ("search_mathlib", "IsCyclic"),
                ("try_standard_tactics", None),
                ("try_proof", "exact foo"),
            ]
        ),
    )

    assert run.telemetry.model_calls == 1        # formalisation
    assert run.telemetry.retrieval_calls == 1
    assert run.telemetry.lean_calls == 2         # tactics + the proof
    assert run.telemetry.seconds >= 0


# ------------------------------------------------------------------ prompt
def test_the_agent_is_told_that_saying_so_is_not_proving():
    factory = scripted_agent([])
    prove("a claim", formalizer=Formalizer(), check=accepts, search=Search(),
          agent_factory=factory)

    assert "does not make it so" in factory.prompt
    assert set(factory.tool_names) == {
        "search_mathlib",
        "try_proof",
        "try_standard_tactics",
    }
