"""Offline tests for Mathlib premise search.

The fixtures below are trimmed copies of real Loogle responses, so the
parser is tested against the actual API shape without any network access.
Retrieval must degrade quietly: every failure path returns [], never raises.
"""

import json

from llm.formalizer import Formalizer
from retrieval.loogle import (
    LoogleSearch,
    Premise,
    extract_queries,
    render_premises,
)

# --- recorded from https://loogle.lean-lang.org/json?q=Nat.Prime -----------
HITS = json.dumps(
    {
        "count": 2163,
        "header": "Found 2163 declarations mentioning Nat.Prime.\n",
        "heartbeats": 21,
        "hits": [
            {
                "doc": "`Nat.Prime p` means that `p` is a prime number.",
                "module": "Mathlib.Data.Nat.Prime.Defs",
                "name": "Nat.Prime",
                "type": " (p : ℕ) : Prop",
            },
            {
                "doc": "Euclid's theorem on the **infinitude of primes**.",
                "module": "Mathlib.Data.Nat.Prime.Infinite",
                "name": "Nat.exists_infinite_primes",
                "type": " (n : ℕ) : ∃ p, n ≤ p ∧ Nat.Prime p",
            },
            {
                "doc": None,
                "module": "Mathlib.Data.Nat.Prime.Defs",
                "name": "Nat.prime_two",
                "type": " : Nat.Prime 2",
            },
        ],
    }
)

# --- recorded from a malformed query --------------------------------------
ERROR = json.dumps(
    {"error": "<input>:1:3: unexpected end of input; expected ')'", "heartbeats": 0}
)


def searcher(payload):
    return LoogleSearch(fetch=lambda url: payload)


def exploding(exception):
    def fetch(url):
        raise exception

    return LoogleSearch(fetch=fetch)


# ------------------------------------------------------------------ parsing
def test_hits_become_premises():
    premises = searcher(HITS).search("Nat.Prime")

    assert [p.name for p in premises] == [
        "Nat.Prime",
        "Nat.exists_infinite_primes",
        "Nat.prime_two",
    ]
    assert premises[0].module == "Mathlib.Data.Nat.Prime.Defs"


def test_a_premise_renders_as_name_plus_type():
    premise = searcher(HITS).search("Nat.Prime")[1]
    assert premise.render() == "Nat.exists_infinite_primes (n : ℕ) : ∃ p, n ≤ p ∧ Nat.Prime p"


def test_a_null_docstring_does_not_become_the_string_none():
    """Loogle sends `"doc": null`, which json turns into None."""
    premises = searcher(HITS).search("Nat.Prime")
    assert premises[2].doc == ""


def test_the_limit_is_respected():
    assert len(searcher(HITS).search("Nat.Prime", limit=2)) == 2


# ------------------------------------------------------- quiet degradation
def test_a_loogle_error_returns_no_premises():
    assert searcher(ERROR).search("(((") == []


def test_malformed_json_returns_no_premises():
    assert searcher("not json at all").search("x") == []


def test_a_network_failure_returns_no_premises():
    """A proof attempt must survive Loogle being unreachable."""
    assert exploding(OSError("no route to host")).search("Nat.Prime") == []


def test_a_timeout_returns_no_premises():
    assert exploding(TimeoutError()).search("Nat.Prime") == []


def test_an_empty_query_makes_no_request():
    def fetch(url):
        raise AssertionError("should not have been called")

    assert LoogleSearch(fetch=fetch).search("   ") == []


# --------------------------------------------------------- query extraction
def test_identifiers_are_pulled_from_a_statement():
    queries = extract_queries(
        "theorem t (G : Type*) [Group G] (h : Nat.Prime (card G)) : IsCyclic G", 10
    )
    assert "Group" in queries
    assert "Nat.Prime" in queries
    assert "IsCyclic" in queries


def test_single_letter_type_variables_are_not_searched():
    """Found by running it for real: `(G : Type*) [Group G]` yielded `G`."""
    queries = extract_queries(
        "theorem t (G : Type*) [Group G] (R : Type*) : IsCyclic G", 10
    )
    assert "G" not in queries
    assert "R" not in queries
    assert "Group" in queries


def test_uninformative_names_are_skipped():
    """Searching `Type` returns thousands of useless results."""
    assert "Type" not in extract_queries("theorem t (G : Type*) : True", 10)


def test_duplicates_are_collapsed():
    queries = extract_queries("Nat.Prime p and Nat.Prime q", 10)
    assert queries.count("Nat.Prime") == 1


def test_extraction_is_bounded():
    statement = " ".join(f"Name{i}" for i in range(20))
    assert len(extract_queries(statement, 4)) == 4


def test_lowercase_lemma_names_are_not_used_as_queries():
    """Those are what we are trying to find, not useful as a search."""
    assert extract_queries("theorem foo (h : bar) : baz", 10) == []


# --------------------------------------------------------------- rendering
def test_rendered_premises_instruct_the_model_to_use_exact_names():
    text = render_premises([Premise(name="Nat.Prime", type=" (p : ℕ) : Prop")])
    assert "do not invent" in text
    assert "Nat.Prime" in text


def test_no_premises_renders_as_nothing_at_all():
    """An empty section beats a section that says 'no results'."""
    assert render_premises([]) == ""


# --------------------------------------------------------- conclusion search
def test_conclusion_patterns_are_tried_before_plain_name_search():
    """Measured: `IsCyclic` buried the useful theorem, `|- IsCyclic _` did not."""
    asked = []

    def fetch(url):
        asked.append(url)
        return HITS

    LoogleSearch(fetch=fetch).premises_for("theorem t : IsCyclic G")

    assert "%7C-" in asked[0] or "|-" in asked[0], "no conclusion pattern tried"


def test_plain_name_search_is_the_fallback():
    """If nothing concludes X, fall back to anything mentioning X."""
    asked = []

    def fetch(url):
        asked.append(url)
        return ERROR if len(asked) == 1 else HITS

    premises = LoogleSearch(fetch=fetch).premises_for("theorem t : Nat.Prime 2")

    assert len(asked) == 2, "fallback did not happen"
    assert premises, "fallback returned nothing"


def test_a_successful_conclusion_search_skips_the_fallback():
    asked = []

    def fetch(url):
        asked.append(url)
        return HITS

    LoogleSearch(fetch=fetch).premises_for("theorem t : Nat.Prime 2")
    assert len(asked) == 1, "queried twice when once sufficed"


# ------------------------------------------------- integration with the prover
class RecordingModel:
    def __init__(self):
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("Reply", (), {"text": "by simp"})()


def test_the_formalizer_puts_retrieved_premises_into_the_prompt():
    model = RecordingModel()
    formalizer = Formalizer(model=model, search=searcher(HITS))

    formalizer.proof("theorem t : Nat.Prime 2", "sketch")

    assert "Nat.exists_infinite_primes" in model.prompts[0]


def test_the_formalizer_works_without_search():
    """Retrieval is an optimisation, not a dependency."""
    model = RecordingModel()
    formalizer = Formalizer(model=model, search=None)

    assert formalizer.proof("theorem t : Nat.Prime 2", "sketch") == "by simp"
    assert "do not invent" not in model.prompts[0]


def test_search_failing_does_not_break_proof_generation():
    model = RecordingModel()
    formalizer = Formalizer(model=model, search=exploding(OSError("down")))

    assert formalizer.proof("theorem t : Nat.Prime 2", "sketch") == "by simp"
