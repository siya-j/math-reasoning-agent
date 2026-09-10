"""Loogle separates search terms with a comma. The model used a space.

Loogle's query language IS Lean, so two adjacent string literals elaborate as
a String applied to a String:

    ?q="Sylow" "card"
      {"error": "Function expected at\n  \"Sylow\"\nbut this term has
                 type\n  String"}

    ?q="Sylow", "card"
      28 hits: not_dvd_card_sylow, card_sylow_modEq_one, Sylow.card_dvd_index

MEASURED over the 911 searches recorded in `eval/evidence/`: 425 returned
nothing, and 291 of those -- 68% of every empty search and 32% of ALL
searches -- used the space form. None of them ever returned a result because
none of them could. Single-name searches are fine by comparison: a bare
CamelCase name is empty 12% of the time and snake_case 29%.

`search_with_suggestions` cannot cover this. It retries using Loogle's
suggestions, which accompany an `unknown identifier` error; this is a type
error and carries none.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval import loogle
from retrieval.loogle import LoogleSearch, comma_separated


# --------------------------------------------------- what it rewrites
@pytest.mark.parametrize("written,sent", [
    ('"Sylow" "card"', '"Sylow", "card"'),
    ('"Sylow" "subsingleton"', '"Sylow", "subsingleton"'),
    ('"a" "b" "c"', '"a", "b", "c"'),
    ('"a"   "b"', '"a", "b"'),
    ('"a"\t"b"', '"a", "b"'),
])
def test_adjacent_literals_get_the_separator(written, sent):
    assert comma_separated(written) == sent


# --------------------------------------------------- what it must not touch
@pytest.mark.parametrize("query", [
    '"Sylow", "card"',            # already correct
    '"Sylow"',                    # one term
    'Nat.Prime',                  # a constant
    'Multiplicative ℝ',           # a TYPE APPLICATION -- the space is meaningful
    '|- _ + _ = _',               # a conclusion pattern
    '"|- ‖_‖ = ‖_‖"',             # a quoted pattern, one literal
    '',
    'Nat.succ_le_of_lt',
])
def test_everything_else_is_left_exactly_alone(query):
    """SAFETY BY CONSTRUCTION. The rewrite fires only where two string
    literals sit adjacent, which Loogle always rejects -- so it can only
    change a query that was guaranteed to fail. `Multiplicative ℝ` is the
    case that matters: there the space IS the application."""
    assert comma_separated(query) == query


def test_a_literal_containing_an_escaped_quote_is_not_split_wrongly():
    assert comma_separated(r'"a\"b" "c"') == r'"a\"b", "c"'


# --------------------------------------------------- it reaches the URL
def test_the_rewrite_happens_on_the_way_out(monkeypatch):
    """Producer side: a normaliser that is written and never applied is the
    shape of bug this repo has shipped before."""
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return '{"count": 0, "hits": []}'

    client = LoogleSearch(fetch=fake_fetch)
    client.search('"Sylow" "card"')

    assert "url" in seen, "no request was made"
    from urllib.parse import unquote
    assert '"Sylow", "card"' in unquote(seen["url"]), unquote(seen["url"])


def test_there_is_exactly_one_place_a_query_becomes_a_url():
    """If a second `?q=` appears it will bypass the normaliser and fail only
    on the queries that need it -- silently."""
    # `?q={` is the f-string that BUILDS a url. Counting bare `?q=` also
    # matches the four example urls in the module's own prose, which is how
    # the first version of this test failed for the wrong reason.
    source = Path(loogle.__file__).read_text(encoding="utf-8")
    assert source.count("?q={") == 1, (
        "more than one query-to-URL site; each must normalise")
    built = [line for line in source.splitlines() if "?q={" in line]
    assert "comma_separated(" in built[0], built[0]


# --------------------------------------------------- against the real service
@pytest.mark.skipif(
    not os.getenv("MRA_TEST_LOOGLE"),
    reason="set MRA_TEST_LOOGLE=1 to check against the live service",
)
def test_the_real_service_accepts_the_rewritten_form():
    """OPT-IN, and for two reasons found the hard way.

    This repo registers no `slow` marker and deselects nothing, so a marked
    test still runs -- which made the whole suite depend on a network the
    module itself promises never to depend on. And it cost 25s per
    invocation, which stretched a two-second mutation pass past its timeout
    and left the tree mutated for minutes.

    `test_the_rewrite_happens_on_the_way_out` already covers the seam with a
    fake fetch. The live evidence belongs in the commit message, not in
    every run.
    """
    client = LoogleSearch()
    try:
        premises = client.search('"Sylow" "subsingleton"')
    except Exception:                      # noqa: BLE001
        pytest.skip("Loogle unreachable")
    if not premises:
        pytest.skip("Loogle reachable but returned nothing; Mathlib may have moved")
    names = [p.name for p in premises]
    assert any("Sylow" in n for n in names), names
