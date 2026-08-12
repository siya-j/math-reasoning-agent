"""Offline tests for Mathlib premise search.

The fixtures below are trimmed copies of real Loogle responses, so the
parser is tested against the actual API shape without any network access.
Retrieval must degrade quietly: every failure path returns [], never raises.
"""

import json
import urllib.parse

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


def test_a_premise_renders_with_its_docstring():
    """Mathlib's docstring often says more than the type signature does."""
    premise = searcher(HITS).search("Nat.Prime")[1]
    rendered = premise.render()
    assert "Nat.exists_infinite_primes" in rendered
    assert "infinitude of primes" in rendered


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


# ------------------------------------------------------ conclusion patterns
def test_the_conclusion_is_taken_from_after_the_final_top_level_colon():
    from retrieval.loogle import conclusion_of

    assert (
        conclusion_of("theorem t (n : ℕ) : ∃ p, n < p ∧ Nat.Prime p")
        == "∃ p, n < p ∧ Nat.Prime p"
    )


def test_colons_inside_binders_are_not_mistaken_for_the_conclusion():
    from retrieval.loogle import conclusion_of

    assert conclusion_of("theorem t (G : Type*) [Group G] : IsCyclic G") == "IsCyclic G"


def test_the_conclusion_pattern_finds_existence_lemmas():
    """The measured failure: `|- Nat.Prime _` has 31 hits and does NOT
    include Nat.exists_infinite_primes, because that concludes an existential.
    `|- ∃ _, _ < _ ∧ Nat.Prime _` returns it as the single hit."""
    from retrieval.loogle import conclusion_pattern

    pattern = conclusion_pattern("theorem t (n : ℕ) : ∃ p, n < p ∧ Nat.Prime p")
    assert pattern.startswith("|- ∃")
    assert "Nat.Prime" in pattern


def test_both_conjunction_orderings_are_issued():
    """Measured on the real failure. Gemini produced

        ∃ p, Nat.Prime p ∧ n < p

    where Mathlib has `∃ p, n ≤ p ∧ Nat.Prime p`. Loogle matches structurally,
    so each ordering finds different lemmas and neither alone is sufficient:

        |- ∃ _, Nat.Prime _ ∧ _   12 hits, including Nat.bertrand
        |- ∃ _, _ ∧ Nat.Prime _    1 hit,  Nat.exists_infinite_primes
    """
    from retrieval.loogle import conclusion_patterns

    patterns = conclusion_patterns(
        "theorem t (n : Nat) : ∃ p, Nat.Prime p ∧ n < p"
    )
    assert "|- ∃ _, Nat.Prime _ ∧ _" in patterns
    assert "|- ∃ _, _ ∧ Nat.Prime _" in patterns


def test_a_hypothesis_narrows_the_query():
    """Measured on the real blocker. `isCyclic_of_prime_card` needs
    `(h : Nat.card α = p)`, and searching hypothesis AND conclusion together
    found it first of ten, where the conclusion alone gave fifty-four."""
    from retrieval.loogle import hypothesis_query

    query = hypothesis_query(
        "theorem t (G : Type*) [Group G] (p : ℕ) (h : Nat.card G = p) : IsCyclic G"
    )
    assert query == "Nat.card _ = _, IsCyclic _"


def test_type_binders_are_not_treated_as_hypotheses():
    """`(G : Type*)` declares a variable; it says nothing about the lemma."""
    from retrieval.loogle import hypothesis_query

    assert hypothesis_query("theorem t (G : Type*) : IsCyclic G") == ""


def test_dotted_names_survive_generalisation():
    """Blanking `card` inside `Nat.card` yields `Nat._`, which matches nothing."""
    from retrieval.loogle import generalise

    assert generalise("Nat.card G = p") == "Nat.card _ = _"
    assert generalise("IsCyclic G") == "IsCyclic _"


def test_the_hypothesis_query_leads_the_ladder():
    asked = []

    def fetch(url):
        asked.append(url)
        return HITS

    LoogleSearch(fetch=fetch).premises_for(
        "theorem t (G : Type*) (p : ℕ) (h : Nat.card G = p) : IsCyclic G"
    )
    assert "Nat.card" in asked[0], "the most selective query was not tried first"


def test_a_conclusion_without_a_conjunction_yields_one_pattern():
    from retrieval.loogle import conclusion_patterns

    assert conclusion_patterns("theorem t [Group G] : IsCyclic G") == ["|- IsCyclic _"]


def test_bare_capitals_are_generalised_in_the_pattern():
    """`G` in `IsCyclic G` is a type variable, not a lemma name."""
    from retrieval.loogle import conclusion_pattern

    assert conclusion_pattern("theorem t [Group G] : IsCyclic G") == "|- IsCyclic _"


def test_a_statement_with_no_conclusion_yields_no_pattern():
    from retrieval.loogle import conclusion_pattern

    assert conclusion_pattern("theorem t") == ""
    assert conclusion_pattern("") == ""


def test_the_conclusion_pattern_is_the_first_query_tried():
    asked = []

    def fetch(url):
        asked.append(url)
        return HITS

    LoogleSearch(fetch=fetch).premises_for(
        "theorem t (n : ℕ) : ∃ p, n < p ∧ Nat.Prime p"
    )
    assert "%E2%88%83" in asked[0], "the existential pattern was not tried first"


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


def test_every_rung_of_the_ladder_is_tried_and_merged():
    """First-hit-wins let one broad query crowd out the precise one."""
    asked = []

    def fetch(url):
        asked.append(url)
        return ERROR if len(asked) == 1 else HITS

    premises = LoogleSearch(fetch=fetch).premises_for("theorem t : Nat.Prime 2")

    assert len(asked) > 1, "stopped after the first query"
    assert premises, "a later rung returned nothing"


def test_results_from_different_rungs_are_deduplicated():
    premises = LoogleSearch(fetch=lambda url: HITS).premises_for(
        "theorem t : Nat.Prime 2"
    )
    names = [p.name for p in premises]
    assert len(names) == len(set(names))


def test_the_total_number_of_premises_is_capped():
    import config

    premises = LoogleSearch(fetch=lambda url: HITS).premises_for(
        "theorem t (n : Nat) : Nat.Prime n ∧ IsCyclic G ∧ Nat.card G = n"
    )
    assert len(premises) <= config.PREMISE_BUDGET


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
    # the shared Lean context is always present; the PREMISE block is not
    assert "declarations exist" not in model.prompts[0]


def test_search_failing_does_not_break_proof_generation():
    model = RecordingModel()
    formalizer = Formalizer(model=model, search=exploding(OSError("down")))

    assert formalizer.proof("theorem t : Nat.Prime 2", "sketch") == "by simp"


# ------------------------------------------------ Loogle's own corrections
def _replies(*payloads):
    """A fetch that returns each payload in turn, recording the queries."""
    seen = []
    queue = list(payloads)

    def fetch(url):
        seen.append(urllib.parse.unquote(url.split("?q=", 1)[1]))
        return json.dumps(queue.pop(0))

    return fetch, seen


def test_an_unparseable_query_is_retried_with_loogles_suggestion():
    """Measured: 19 of 20 searches returned nothing and Loogle knew the answer.

    A bare identifier is a CONSTANT lookup, so a name FRAGMENT must be
    quoted. Loogle says so in the error, and we used to throw it away.
    """
    fetch, seen = _replies(
        {"error": "unknown identifier 'exists_infinite_primes'",
         "suggestions": ['"exists_infinite_primes"', "Nat.exists_infinite_primes"]},
        {"count": 1, "hits": [{"name": "Nat.exists_infinite_primes",
                               "type": " (n : ℕ) : ∃ p, n ≤ p ∧ Nat.Prime p",
                               "module": "Mathlib.Data.Nat.Prime.Infinite"}]},
    )
    found = LoogleSearch(fetch=fetch).search("exists_infinite_primes")

    assert [p.name for p in found] == ["Nat.exists_infinite_primes"]
    assert seen[1] == '"exists_infinite_primes"', "the suggestion was not used"


def test_the_remaining_suggestions_are_handed_back():
    """`Basis` no longer exists; `Module.Basis` is in the suggestion list."""
    fetch, _ = _replies(
        {"error": "unknown identifier 'Basis'",
         "suggestions": ['"Basis"', "Module.Basis", "PowerBasis.basis"]},
        {"count": 0, "hits": []},
    )
    found, suggestions = LoogleSearch(fetch=fetch).search_with_suggestions("Basis")

    assert found == []
    assert "Module.Basis" in suggestions


def test_a_failing_suggestion_is_not_chased_further():
    """Otherwise a bad name walks the library one suggestion at a time."""
    fetch, seen = _replies(
        {"error": "unknown identifier 'a'", "suggestions": ["b"]},
        {"error": "unknown identifier 'b'", "suggestions": ["c"]},
    )
    assert LoogleSearch(fetch=fetch).search("a") == []
    assert len(seen) == 2, "the retry chained instead of stopping"
