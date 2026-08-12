"""Offline tests for the agent budget. No model, no Lean, no waiting.

Observed on near-mathlib: a goal ran without terminating and had to be
interrupted by hand, producing no proof, no verdict and no record.

`test_a_runaway_agent_is_stopped_even_if_it_ignores_the_warning` is the one
that matters. A polite request to stop is not a guarantee; the raise is.
"""

import pytest

import config

from domain.verdict import Verdict, VerificationStatus as S
from pipeline.agentic_prover import prove
from pipeline.proof_tools import Budget, BudgetExhausted, ProofLog, make_proof_tools

ACCEPTED = Verdict(S.TRUE, "lean", "accepted")
REJECTED = Verdict(S.UNKNOWN, "lean", "error: no")


class Formalizer:
    def statement(self, goal):
        return "theorem t : True"


def accepts(statement, proof):
    return ACCEPTED


def rejects(statement, proof):
    return REJECTED


def runaway(calls=1000, tool="try_proof"):
    """An agent that never stops calling a tool on its own."""

    def factory(model, tools, system_prompt):
        by_name = {t.__name__: t for t in tools}

        class Agent:
            def invoke(self, payload):
                for index in range(calls):
                    by_name[tool](f"attempt {index}")
                return {"messages": []}

        return Agent()

    return factory


# ----------------------------------------------------------------- the bound
def test_a_runaway_agent_is_stopped_even_if_it_ignores_the_warning():
    """The load-bearing test: termination must not depend on the model."""
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=rejects,
        search=None,
        agent_factory=runaway(),
        budget=Budget(max_tool_calls=5, max_lean_calls=99, grace=2),
    )

    assert not run.proved
    assert len(run.attempts) == 5, "the budget did not bound the compilations"
    assert "Stopped early" in run.verdict.detail


def test_the_tool_budget_is_respected():
    log = ProofLog(statement="theorem t : True",
                   budget=Budget(max_tool_calls=2, grace=99))
    tools = {t.__name__: t for t in make_proof_tools(log, rejects)}

    tools["try_proof"]("one")
    tools["try_proof"]("two")
    third = tools["try_proof"]("three")

    assert len(log.attempts) == 2
    assert third.startswith("STOP")


def test_lean_calls_are_budgeted_separately():
    """They cost about twenty seconds each; searches cost milliseconds."""
    log = ProofLog(statement="theorem t : True",
                   budget=Budget(max_tool_calls=99, max_lean_calls=1, grace=99))
    tools = {t.__name__: t for t in make_proof_tools(log, rejects)}

    tools["try_proof"]("one")
    assert tools["try_proof"]("two").startswith("STOP")
    # searching is still allowed — only the expensive budget is spent
    assert not tools["search_mathlib"]("anything").startswith("STOP")


# -------------------------------------------------- search cannot starve proof
def test_searching_cannot_consume_the_whole_tool_budget():
    """Measured: 20 tool calls spent on search, 0 compiles, nothing proved."""
    log = ProofLog(
        statement="theorem t : True",
        budget=Budget(max_tool_calls=20, max_searches=3,
                      max_consecutive_searches=99, grace=99),
    )
    tools = {t.__name__: t for t in make_proof_tools(log, rejects)}

    for _ in range(3):
        assert not tools["search_mathlib"]("q").startswith("ENOUGH")
    assert tools["search_mathlib"]("q").startswith("ENOUGH")

    # the run is NOT over — the compilations were never touched
    assert not tools["try_proof"]("a proof").startswith("STOP")


def test_a_redirect_is_still_charged_so_it_cannot_loop_forever():
    log = ProofLog(
        statement="theorem t : True",
        budget=Budget(max_tool_calls=6, max_searches=1, grace=99),
    )
    tools = {t.__name__: t for t in make_proof_tools(log, rejects)}

    for _ in range(6):
        tools["search_mathlib"]("q")
    assert tools["search_mathlib"]("q").startswith("STOP"), (
        "redirects were free, so an agent that only searches never terminates"
    )


def test_compiling_resets_the_consecutive_search_count():
    """The cap is on searching WITHOUT compiling, not on searching."""
    log = ProofLog(
        statement="theorem t : True",
        budget=Budget(max_tool_calls=99, max_searches=99,
                      max_consecutive_searches=2, grace=99),
    )
    tools = {t.__name__: t for t in make_proof_tools(log, rejects)}

    tools["search_mathlib"]("q")
    tools["search_mathlib"]("q")
    assert tools["search_mathlib"]("q").startswith("ENOUGH")

    tools["try_proof"]("something")
    assert not tools["search_mathlib"]("q").startswith("ENOUGH")


def test_the_time_budget_is_respected():
    log = ProofLog(
        statement="theorem t : True",
        budget=Budget(max_seconds=-1, grace=99),   # already over
    )
    tools = {t.__name__: t for t in make_proof_tools(log, rejects)}

    assert tools["try_proof"]("anything").startswith("STOP")
    assert log.attempts == [], "work was done after the time budget expired"


def test_the_stop_message_names_the_limit_that_was_hit():
    budget = Budget(max_tool_calls=0, grace=99)
    assert "tool budget" in budget.spend()

    budget = Budget(max_lean_calls=0, grace=99)
    assert "compilation budget" in budget.spend(lean=True)

    budget = Budget(max_seconds=-1, grace=99)
    assert "time budget" in budget.spend()


def test_a_compilation_is_refused_when_too_little_time_remains():
    """A compile begun near the deadline still runs a full LEAN_TIMEOUT past it."""
    budget = Budget(max_seconds=config.LEAN_TIMEOUT / 2, grace=99)
    assert budget.spend(lean=True).startswith("STOP")
    assert not budget.spend(search=True), "cheap work was blocked too"


def test_the_clock_gets_less_grace_than_the_other_budgets():
    """Each graced round trip is spent in the currency that ran out."""
    budget = Budget(max_seconds=-1, grace=3)
    assert budget.spend().startswith("STOP")
    with pytest.raises(BudgetExhausted):
        budget.spend()


def test_grace_runs_out_and_then_it_raises():
    budget = Budget(max_tool_calls=0, grace=1)
    assert budget.spend().startswith("STOP")
    with pytest.raises(BudgetExhausted):
        budget.spend()


# ------------------------------------------------- a proof survives the bound
def test_a_proof_found_before_the_budget_ran_out_is_kept():
    """Stopping the agent must not discard a compilation that succeeded."""

    def factory(model, tools, system_prompt):
        by_name = {t.__name__: t for t in tools}

        class Agent:
            def invoke(self, payload):
                by_name["try_proof"]("the good one")   # accepted
                for index in range(100):               # then runs away
                    by_name["try_proof"](f"noise {index}")
                return {"messages": []}

        return Agent()

    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,
        search=None,
        agent_factory=factory,
        budget=Budget(max_tool_calls=3, grace=1),
    )

    assert run.proved, "a successful proof was lost when the budget ran out"
    assert run.proof == "the good one"


# --------------------------------------------------------------- reporting
def test_running_out_of_budget_is_not_reported_as_having_tried_and_failed():
    """They are different results; conflating them misreports a proof rate."""
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=rejects,
        search=None,
        agent_factory=runaway(),
        budget=Budget(max_tool_calls=2, grace=1),
    )
    assert "Stopped early" in run.verdict.detail
    assert "tool budget" in run.verdict.detail


def test_an_agent_that_finishes_early_reports_no_budget_problem():
    def factory(model, tools, system_prompt):
        by_name = {t.__name__: t for t in tools}

        class Agent:
            def invoke(self, payload):
                by_name["try_proof"]("one honest attempt")
                return {"messages": []}

        return Agent()

    run = prove(
        "a claim", formalizer=Formalizer(), check=rejects, search=None,
        agent_factory=factory, budget=Budget(max_tool_calls=20),
    )
    assert "Stopped early" not in run.verdict.detail
