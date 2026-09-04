"""A search that finds nothing falls back to the goal's own shape.

MEASURED, on eval/results/putnam-run2.json. The agent issued 61 searches
across five goals:

  * NOT ONE used the `|- <shape>` form the prompt calls the thing that
    actually finds lemmas -- every query was a bare name ("Routh", "volume",
    "simplex", "toReal");
  * 22 of the 61 (36%) returned nothing at all;
  * on `putnam_1962_a3` it was 17 empty out of 26, after which the model
    stopped searching and fabricated `dummy_lemma : 1 = 1`.

`loogle.premises_for` has always implemented the better ladder, with measured
evidence in its own docstring (2163 hits for a bare name, 54 for the
conclusion, 10 with a hypothesis and the right lemma first), and
`seed_premises` runs it ONCE when the statement is first checked. Nothing ran
it again, so a model searching badly mid-proof got nothing and moved on.

This is the third time in this project that a rule stated in the prompt was
declined in practice -- after "DO NOT WRITE A NEW SKELETON" and the
resubmission rule. The answer each time has been the same: stop asking, start
doing.
"""

import pytest

from math_v2.core import log, retrieval
from retrieval.loogle import Premise

GOAL = "theorem mra_goal (G : Type*) [Group G] : IsCyclic G"


def premise(name, type_="∀ (n : ℕ), n < 2 * n"):
    """`rank` sorts lemmas ahead of definitions using `provides_a_proof`,
    which reads the TYPE -- so a fixture with an empty type would be ranked as
    a definition and quietly reordered."""
    return Premise(name=name, type=type_, module="Mathlib.Test", doc="")


class Search:
    """A Loogle whose two entry points can be answered independently."""

    def __init__(self, direct=(), ladder=(), explode=False):
        self.direct = list(direct)
        self.ladder = list(ladder)
        self.explode = explode
        self.ladder_calls = []

    def search_with_suggestions(self, query, limit=None):
        return list(self.direct), []

    def premises_for(self, statement):
        self.ladder_calls.append(statement)
        if self.explode:
            raise RuntimeError("loogle is down")
        return list(self.ladder)


@pytest.fixture
def workdir(tmp_path):
    path = str(tmp_path)
    log.set_goal(path, GOAL)
    return path


def test_an_empty_search_is_answered_with_the_goals_shape(workdir):
    """THE measured case: 22 of 61 searches returned nothing and the turn was
    spent on an apology."""
    search = Search(direct=[], ladder=[premise("isCyclic_of_prime_card")])

    result = retrieval.search_mathlib(workdir, '"Routh"', search)

    assert result["outputs"]["from_goal_shape"] is True
    assert result["outputs"]["found"][0]["name"] == "isCyclic_of_prime_card"
    assert "GOAL'S OWN SHAPE" in result["message"]


def test_the_ladder_runs_against_the_goal_the_model_is_working_on(workdir):
    """`current_goal`, not the declared one: mid-proof the model may be on a
    lemma, and premises for the original theorem would be the wrong answer."""
    log.set_goal(workdir, "theorem sub_goal : Nat.Prime 7")
    search = Search(direct=[], ladder=[premise("Nat.prime_def")])

    retrieval.search_mathlib(workdir, '"nothing"', search)

    assert search.ladder_calls == ["theorem sub_goal : Nat.Prime 7"]


def test_a_search_that_found_something_does_not_trigger_the_ladder(workdir):
    """The fallback is for empty results only. Running it on every search
    would spend HTTP calls to second-guess a query that worked."""
    search = Search(direct=[premise("Nat.exists_infinite_primes")],
                    ladder=[premise("should_not_appear")])

    result = retrieval.search_mathlib(workdir, '"exists_infinite"', search)

    assert search.ladder_calls == []
    assert result["outputs"].get("from_goal_shape") is None


def test_rescued_premises_are_remembered_like_any_other(workdir):
    """They must reach the premise store, or the next attempt cannot cite
    them and the rescue is cosmetic."""
    search = Search(direct=[], ladder=[premise("isCyclic_of_prime_card")])

    retrieval.search_mathlib(workdir, '"Routh"', search)

    names = [p["name"] for p in log.read(workdir)["premises"]]
    assert "isCyclic_of_prime_card" in names


def test_the_trace_distinguishes_a_rescue_from_an_ordinary_search(workdir):
    """Otherwise the run's own record cannot answer "how often did the model's
    query fail", which is the number that says whether this is still needed."""
    search = Search(direct=[], ladder=[premise("isCyclic_of_prime_card")])

    retrieval.search_mathlib(workdir, '"Routh"', search)

    trace = " ".join(log.read(workdir)["trace"])
    assert "nothing; goal shape ->" in trace


def test_noise_is_filtered_out_of_the_rescue(workdir):
    """The same filter the ordinary path applies. A rescue that hands back
    `Lean.ConstantInfo` has spent the turn no better than the empty answer."""
    search = Search(direct=[], ladder=[
        premise("Lean.Macro.MethodsRef"), premise("isCyclic_of_prime_card")])

    result = retrieval.search_mathlib(workdir, '"Routh"', search)

    names = [f["name"] for f in result["outputs"]["found"]]
    assert "Lean.Macro.MethodsRef" not in names
    assert "isCyclic_of_prime_card" in names


def test_a_ladder_failure_falls_back_to_the_honest_empty_answer(workdir):
    """Retrieval is an optimisation and may never take a run down."""
    search = Search(direct=[], ladder=[], explode=True)

    result = retrieval.search_mathlib(workdir, '"Routh"', search)

    assert result["ok"] is True
    assert result["outputs"]["found"] == []
    assert "nothing either" in result["message"]


def test_no_goal_yet_means_no_ladder(workdir):
    """Before `check_statement` there is nothing to take a shape from."""
    log.set_goal(workdir, "")
    search = Search(direct=[], ladder=[premise("anything")])

    retrieval.search_mathlib(workdir, '"Routh"', search)

    assert search.ladder_calls == []
