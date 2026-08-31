"""Offline tests for `retrieval/usage_examples.py` (the DRIFT-style "illustrate"
step): finding a real citation of a Mathlib name in Mathlib's own source.

No real Mathlib checkout is needed. Each test builds a small synthetic
`.lake/packages/mathlib/Mathlib` tree under `tmp_path` and points
`MRA_LEAN_PROJECT` (or `MRA_MATHLIB_SOURCE`) at it, so the walk and the
matching rules are exercised against known, controlled content.
"""

from retrieval import usage_examples


def _mathlib_tree(tmp_path):
    """An empty `.lake/packages/mathlib/Mathlib` directory under `tmp_path`."""
    root = tmp_path / ".lake" / "packages" / "mathlib" / "Mathlib"
    root.mkdir(parents=True)
    return root


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------- finding a project
def test_no_project_configured_returns_nothing(monkeypatch):
    monkeypatch.delenv("MRA_LEAN_PROJECT", raising=False)
    monkeypatch.delenv("MRA_MATHLIB_SOURCE", raising=False)
    assert usage_examples.find("Nat.Prime") == ""


def test_a_project_without_a_mathlib_checkout_returns_nothing(tmp_path, monkeypatch):
    """`MRA_LEAN_PROJECT` set, but `lake exe cache get` was never run there."""
    monkeypatch.delenv("MRA_MATHLIB_SOURCE", raising=False)
    assert usage_examples.find("Nat.Prime", project=str(tmp_path)) == ""


def test_mra_mathlib_source_overrides_the_default_layout(tmp_path, monkeypatch):
    """A layout that does not match `.lake/packages/mathlib/Mathlib`."""
    root = tmp_path / "somewhere-else"
    root.mkdir()
    _write(root, "Foo.lean", "theorem uses_it : True := by exact trivial_helper\n")
    monkeypatch.setenv("MRA_MATHLIB_SOURCE", str(root))

    found = usage_examples.find("trivial_helper")
    assert "Foo.lean" in found
    assert "trivial_helper" in found


# ------------------------------------------------------------- empty inputs
def test_an_empty_name_returns_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MRA_MATHLIB_SOURCE", str(_mathlib_tree(tmp_path)))
    assert usage_examples.find("") == ""
    assert usage_examples.find("   ") == ""


# --------------------------------------------------------------- citations
def test_a_real_citation_is_found(tmp_path, monkeypatch):
    root = _mathlib_tree(tmp_path)
    _write(
        root, "Analysis/Basic.lean",
        "theorem uses_it (x : ℝ) : x ≤ x :=\n"
        "  le_of_eq (Real.sq_sqrt (le_of_lt (by positivity)))\n",
    )
    monkeypatch.setenv("MRA_LEAN_PROJECT", str(tmp_path))

    found = usage_examples.find("Real.sq_sqrt")
    assert "Analysis/Basic.lean" in found
    assert "Real.sq_sqrt" in found


def test_nothing_found_returns_empty_string(tmp_path, monkeypatch):
    root = _mathlib_tree(tmp_path)
    _write(root, "Analysis/Basic.lean", "theorem unrelated : True := by trivial\n")
    monkeypatch.setenv("MRA_LEAN_PROJECT", str(tmp_path))

    assert usage_examples.find("Nat.exists_infinite_primes") == ""


def test_the_declaring_line_itself_is_not_returned_as_a_citation(tmp_path,
                                                                  monkeypatch):
    """Loogle already showed the declaration; this module exists to add a USE."""
    root = _mathlib_tree(tmp_path)
    _write(
        root, "Data/Nat/Prime.lean",
        "theorem Nat.exists_infinite_primes (n : ℕ) : ∃ p, n ≤ p ∧ Nat.Prime p "
        ":= by\n  sorry\n",
    )
    monkeypatch.setenv("MRA_LEAN_PROJECT", str(tmp_path))

    assert usage_examples.find("Nat.exists_infinite_primes") == ""


def test_a_citation_is_preferred_over_a_later_declaration(tmp_path, monkeypatch):
    """A file may both cite the name (searched for) and declare something else
    that merely mentions it in passing -- the declaration guard must not
    reject a line that isn't the declaration of THIS name."""
    root = _mathlib_tree(tmp_path)
    _write(
        root, "Data/Nat/Consequences.lean",
        "theorem uses_primes : True := by\n"
        "  have := Nat.exists_infinite_primes 5\n  trivial\n",
    )
    monkeypatch.setenv("MRA_LEAN_PROJECT", str(tmp_path))

    found = usage_examples.find("Nat.exists_infinite_primes")
    assert "have := Nat.exists_infinite_primes 5" in found


# ------------------------------------------------------- leaf-name fallback
def test_an_unqualified_citation_inside_a_namespace_is_found_by_its_leaf(
    tmp_path, monkeypatch
):
    """MEASURED: `NNReal.sqrt_mul_le_half_add` is a real, cited lemma, but the
    fully-qualified name never appears literally in source written from
    inside `namespace NNReal ... end NNReal`, which is how Mathlib is
    ordinarily written."""
    root = _mathlib_tree(tmp_path)
    _write(
        root, "Analysis/SpecialFunctions/ArithmeticGeometricMean.lean",
        "namespace NNReal\n\n"
        "theorem amgm_step (x y : ℝ≥0) : True := by\n"
        "  have := sqrt_mul_le_half_add x y\n  trivial\n\n"
        "end NNReal\n",
    )
    monkeypatch.setenv("MRA_LEAN_PROJECT", str(tmp_path))

    found = usage_examples.find("NNReal.sqrt_mul_le_half_add")
    assert "sqrt_mul_le_half_add" in found
    assert "ArithmeticGeometricMean.lean" in found


def test_the_qualified_name_is_tried_before_the_leaf(tmp_path, monkeypatch):
    """When both exist, prefer the exact citation over the ambiguous leaf."""
    root = _mathlib_tree(tmp_path)
    _write(
        root, "Data/Nat/Prime.lean",
        "theorem uses_the_leaf_alone : True := by\n"
        "  have := prime_two\n  trivial\n",
    )
    _write(
        root, "Analysis/Basic.lean",
        "theorem uses_it_qualified : True := by\n"
        "  have := Nat.prime_two\n  trivial\n",
    )
    monkeypatch.setenv("MRA_LEAN_PROJECT", str(tmp_path))

    found = usage_examples.find("Nat.prime_two")
    assert "Nat.prime_two" in found


# ------------------------------------------------------------- degradation
def test_never_raises_on_an_unreadable_file(tmp_path, monkeypatch):
    root = _mathlib_tree(tmp_path)
    path = _write(root, "Broken.lean", "theorem t : True := by trivial\n")
    path.chmod(0o000)
    monkeypatch.setenv("MRA_LEAN_PROJECT", str(tmp_path))

    try:
        assert usage_examples.find("t") == ""
    finally:
        path.chmod(0o644)


def test_max_files_bounds_the_walk(tmp_path, monkeypatch):
    """A match beyond `max_files` is never seen -- this is a cost bound, not
    a correctness guarantee, and the caller may trade completeness for it."""
    root = _mathlib_tree(tmp_path)
    _write(root, "Z_only_match.lean", "theorem uses_it : True := by exact q\n")
    monkeypatch.setenv("MRA_LEAN_PROJECT", str(tmp_path))

    assert usage_examples.find("q", max_files=0) == ""
