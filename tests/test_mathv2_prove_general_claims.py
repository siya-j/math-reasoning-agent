"""The prompt's routing rule: prove general claims, compute concrete ones. Offline.

MEASURED in Aura, 2026-09-25: "Is (x+1)^2 = x^2 + 2x + 1 for all real x?" was
answered with one SymPy check and `not_formalized`. The prompt had filed
identities under "a computational claim needs one check", so the agent did
exactly what it was told -- and a universally quantified identity is a
theorem, which the user expected to see proved. Primality and evaluation stay
SymPy-only: Lean adds nothing there.
"""

from math_v2.prompt import system_prompt


def _section(text, heading):
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:end if end != -1 else None]


def test_a_general_claim_goes_on_to_lean_after_sympy_says_true():
    rule = _section(system_prompt(), "## How to think about a claim").replace("\n", " ")
    assert "GENERAL CLAIM" in rule
    assert "for all real x" in rule
    assert "user asks for a proof" in rule
    assert "SymPy TRUE is reconnaissance, not the answer" in rule
    assert "`try_standard_tactics`" in rule


def test_a_concrete_computation_stays_sympy_only():
    rule = _section(system_prompt(), "## How to think about a claim").replace("\n", " ")
    assert "CONCRETE COMPUTATION" in rule
    for example in ("2^32 + 1 is prime", "factorisation of a polynomial"):
        assert example in rule
    assert "Lean adds nothing" in rule


def test_finishing_points_the_agent_at_lemmas_used():
    assert "`lemmas_used`" in system_prompt()
