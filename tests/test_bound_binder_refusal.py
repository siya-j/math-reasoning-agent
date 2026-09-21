"""A leading `intro` of binders the signature already bound is answerable.

MEASURED on heldout-even-63: 17 of 63 goals spent one compile each on

    Tactic `introN` failed: There are no additional binders or `let` bindings
    in the goal to introduce

roughly 7% of the run's 243 compilations, every one of them a proof that opened
`intro z w h0 h1` against a statement whose own signature binds `z w h0 h1`.
A signature binder is in context before the body runs, so the tactic fails
before any mathematics is attempted and the compile teaches nothing.

THE RISK THIS FILE EXISTS TO PIN. `theorem f (n : ℕ) : ∀ n, P n` is legal
Lean, and there `intro n` is CORRECT even though `n` is a signature binder.
A guard that only compared names against the signature would refuse a good
proof -- strictly worse than the wasted compile it set out to save. So the
conclusion is checked too, and anything that might still leave something to
introduce means no refusal.
"""

from math_v2.core.proving import (
    _bound_binders_refusal,
    _intros_bound_binders,
)

# The measured shape, from `Putnam_exercise_2020_b5`.
REBOUND = (
    "lemma mra_lemma_1 (z w : Fin 4 → ℂ) (h0 : z 0 * w 0 = w 0 - 1) "
    "(h1 : z 1 * w 1 = w 1 - 1) : z 0 * w 0 * (w 1 - 1) = 0"
)


def test_it_fires_on_the_shape_that_was_measured():
    assert _intros_bound_binders(REBOUND, "by\n  intro z w h0 h1\n  ring") == (
        "z", "w", "h0", "h1"
    )


def test_a_leading_by_on_the_same_line_is_still_seen():
    assert _intros_bound_binders(REBOUND, "by intro z w h0 h1") == (
        "z", "w", "h0", "h1"
    )


# ------------------------------------------------- when it must stay silent
def test_a_shadowing_binder_in_the_conclusion_is_left_alone():
    """`intro n` is CORRECT here, and `n` is a signature binder."""
    statement = "theorem f (n : ℕ) : ∀ n : ℕ, n = n"
    assert _intros_bound_binders(statement, "by\n  intro n\n  rfl") == ()


def test_an_arrow_in_the_conclusion_is_left_alone():
    statement = "theorem f (p q : Prop) (hp : p) : q → p"
    assert _intros_bound_binders(statement, "by\n  intro hq\n  exact hp") == ()


def test_a_destructuring_intro_is_left_alone():
    """`⟨a, b⟩` is a pattern, not a name, so nothing here is knowable."""
    assert _intros_bound_binders(REBOUND, "by\n  intro ⟨a, b⟩\n  ring") == ()


def test_a_bare_intro_with_no_names_is_left_alone():
    assert _intros_bound_binders(REBOUND, "by\n  intro\n  ring") == ()


def test_a_name_the_signature_does_not_bind_is_left_alone():
    assert _intros_bound_binders(REBOUND, "by\n  intro k\n  ring") == ()


def test_a_proof_that_does_not_open_with_intro_is_left_alone():
    assert _intros_bound_binders(REBOUND, "by\n  ring") == ()


def test_an_unparseable_statement_is_left_alone():
    assert _intros_bound_binders("not a theorem at all", "by intro x") == ()


# ------------------------------------------------------------ the refusal
def test_the_refusal_carries_an_error_key_so_it_is_refunded():
    """`test_refusals_are_free` keys the refund on the PRESENCE of `error`."""
    refusal = _bound_binders_refusal(("z", "w"))
    assert refusal["error"] == "binders_already_bound"
    assert refusal["ok"] is False
    assert refusal["outputs"]["accepted"] is False


def test_the_refusal_names_the_binders_and_says_what_to_do():
    message = _bound_binders_refusal(("z", "w"))["message"]
    assert "`z`" in message and "`w`" in message
    assert "not compiled" in message
