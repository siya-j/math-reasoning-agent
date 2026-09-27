"""The faithfulness lint ignores names and accepts constants the wording states. Offline.

MEASURED through Aura, 2026-09-27 (nine harder problems, all correct in the end):
  * "(21n+4)/(14n+3) is irreducible" was refused for "1, 1959": 1959 came from the
    theorem's own name (`imo_1959_p1`), and 1 is the gcd that "irreducible" means.
  * "for all non-negative reals a and b" written with `(ha : 0 ≤ a)` was refused
    for "0", and the agent retreated to NNReal to get past the lint.
Both cost a round trip. The lint's job is unchanged: a statement that is NOT what
was asked, because it carries a number the question does not state, is refused.
"""

import pytest

from math_v2.core.verdict import faithfulness_failure

IRREDUCIBLE = "Prove that for every natural number n, the fraction (21n+4)/(14n+3) is irreducible."
AM_GM = "Prove that for all non-negative reals a and b, (a+b)/2 ≥ √(ab)."
PRIME_24 = "Prove that if p is a prime greater than 3, then 24 divides p² − 1."
FOUR_POW = "Prove that 3 divides 4^n − 1 for every natural number n."
SIX = "Prove that n(n+1)(n+2) is divisible by 6 for every natural number n."


# ------------------------------------------------------------ now accepted


def test_a_year_in_the_theorem_name_is_not_a_value():
    stmt = "theorem imo_1959_p1 (n : ℕ) : Nat.gcd (21 * n + 4) (14 * n + 3) = 1"
    assert faithfulness_failure(stmt, IRREDUCIBLE) == ""


def test_irreducible_states_a_gcd_of_one():
    stmt = "theorem t (n : ℕ) : Nat.Coprime (21 * n + 4) (14 * n + 3) ∨ Nat.gcd (21 * n + 4) (14 * n + 3) = 1"
    assert faithfulness_failure(stmt, IRREDUCIBLE) == ""


def test_non_negative_states_zero():
    stmt = "theorem am_gm (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : Real.sqrt (a * b) ≤ (a + b) / 2"
    assert faithfulness_failure(stmt, AM_GM) == ""


def test_hypothesis_names_with_digits_are_names():
    stmt = "theorem t (p : ℕ) (h1 : p.Prime) (h2 : 3 < p) : 24 ∣ p ^ 2 - 1"
    assert faithfulness_failure(stmt, PRIME_24) == ""


def test_a_superscript_exponent_is_the_digit_it_shows():
    """"p² − 1" states the exponent 2; `p ^ 2 - 1` is its faithful transcription."""
    stmt = "theorem t (p : ℕ) (hp : p.Prime) (h : 3 < p) : 24 ∣ p ^ 2 - 1"
    assert faithfulness_failure(stmt, PRIME_24) == ""
    # ...and a different exponent is still invented.
    assert "3" not in faithfulness_failure(stmt, PRIME_24)
    cube = "theorem t (p : ℕ) (hp : p.Prime) (h : 3 < p) : 24 ∣ p ^ 4 - 1"
    assert "4" in faithfulness_failure(cube, PRIME_24)


@pytest.mark.parametrize("claim", [
    "Prove that x^2 ≥ 0 for every positive real x.",
    "Prove that 8n+3 and 5n+2 are coprime for every natural number n.",
    "Show that 8n+3 and 5n+2 are relatively prime.",
])
def test_each_implying_word_is_recognised(claim):
    if "coprime" in claim or "relatively" in claim:
        stmt = "theorem t (n : ℕ) : Nat.gcd (8 * n + 3) (5 * n + 2) = 1"
    else:
        stmt = "theorem t (x : ℝ) (hx : 0 < x) : x ^ 2 ≥ 0"
    assert faithfulness_failure(stmt, claim) == ""


# ------------------------------------------------------------ still refused


def test_an_added_restriction_is_still_refused():
    """Narrowing "every natural number n" to n ≥ 2 proves less than was asked."""
    stmt = "theorem t (n : ℕ) (hn : 2 ≤ n) : 3 ∣ 4 ^ n - 1"
    assert "2" in faithfulness_failure(stmt, FOUR_POW)


def test_a_narrower_domain_at_the_new_boundary_is_still_refused():
    """"non-negative" states 0, not 1: a ≥ 1 is a narrower domain."""
    stmt = "theorem t (a b : ℝ) (ha : 1 ≤ a) (hb : 1 ≤ b) : Real.sqrt (a * b) ≤ (a + b) / 2"
    assert "1" in faithfulness_failure(stmt, AM_GM)


def test_zero_is_not_implied_without_the_word():
    """"every natural number n" does not say positive: 0 < n drops n = 0."""
    stmt = "theorem t (n : ℕ) (hn : 0 < n) : 6 ∣ n * (n + 1) * (n + 2)"
    assert "0" in faithfulness_failure(stmt, SIX)


def test_a_weakened_hypothesis_is_still_refused():
    """"greater than 3" replaced by "greater than 5" leaves out p = 5."""
    stmt = "theorem t (p : ℕ) (hp : p.Prime) (h : 5 < p) : 24 ∣ p ^ 2 - 1"
    assert "5" in faithfulness_failure(stmt, PRIME_24)


def test_a_changed_conclusion_is_still_refused():
    """24 ∣ weakened to 8 ∣ is a different, easier theorem."""
    stmt = "theorem t (p : ℕ) (hp : p.Prime) (h : 3 < p) : 8 ∣ p ^ 2 - 1"
    assert "8" in faithfulness_failure(stmt, PRIME_24)


def test_a_name_with_digits_does_not_hide_a_literal():
    stmt = "theorem t1 : x = 7"
    assert "7" in faithfulness_failure(stmt, "is x equal to x?")
