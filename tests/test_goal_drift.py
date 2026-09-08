"""An attempt at a statement nobody declared cannot settle the goal.

MEASURED, and it cost a solved goal. `hard-sum-odd-squares` asks informally
for "the sum of the first n odd numbers equals n squared" and leaves the
formalisation to the agent. It produced three, all mathematically correct:

    1  ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2          checked, PROVED
    2  ∑ i ∈ Finset.filter Odd (Finset.range (n+n)), i = n ^ 2   checked
    3  ∑ i ∈ Finset.range n, (i + i).succ = n * n         never checked, PROVED

`log.declared_goal` is the LAST statement check, so #2 was the scored goal and
neither accepted proof matched it. The run recorded `not_proved` for a theorem
it had proved twice, and spent twelve compilations getting there without ever
being told the work could not count. Across the thirty-five rescued
workspaces, NINE show an accepted proof that is not of the declared goal;
seven recovered by later proving the declared one, two did not.

WHY REFUSED AND NOT CREDITED: `alg-square-nonneg` submitted `x * x ≥ x - x`
against a declared `0 ≤ x * x` -- trivially true, and a weakening rather than
a reformulation. Telling that apart from #3 needs a judgement about
mathematical equivalence, which is exactly what `core/verdict.py` refuses to
take from prose.
"""

import asyncio

import pytest

from math_v2.core import log, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

DECLARED = "theorem sum_odd_eq_sq (n : ℕ) : ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2"
DRIFTED = "theorem sum_odd_eq_sq (n : ℕ) : ∑ i ∈ Finset.range n, (i + i).succ = n * n"


def run(coro):
    return asyncio.run(coro)


async def accepts(source):
    return LeanResult(LeanOutcome.COMPILED, "")


async def rejects(source):
    return LeanResult(LeanOutcome.ERRORS, "3:2: error: unsolved goals")


@pytest.fixture
def declared(tmp_path):
    """A workdir whose declared goal is DECLARED, set the only way that
    counts: a real `check_statement` leaving a STATEMENT_CHECK record."""
    workdir = str(tmp_path)
    log.clear(workdir)
    run(proving.check_statement(workdir, DECLARED, accepts))
    assert log.declared_goal(workdir).strip() == DECLARED
    return workdir


# ------------------------------------------------------- THE regression
def test_a_proof_of_a_different_statement_is_refused(declared):
    """The `hard-sum-odd-squares` sequence: prove formalisation #3 while #1 is
    the declared goal. Before this guard the compiler accepted it, the record
    kept it, and nothing scored."""
    result = run(proving.try_proof(declared, DRIFTED, "by simp", accepts))

    assert result["error"] == "not_the_goal", result.get("message", "")
    assert "check_statement" in result["message"]
    assert "try_lemma" in result["message"]
    assert not log.records(declared, log.PROOF), (
        "a refusal was recorded as an attempt at the goal"
    )


def test_the_declared_goal_itself_is_allowed(declared):
    """The control. Passing the goal explicitly is the ordinary case."""
    result = run(proving.try_proof(declared, DECLARED, "by simp", accepts))
    assert result.get("error") is None, result
    assert len(log.records(declared, log.PROOF)) == 1


def test_reindenting_the_goal_is_not_drift(declared):
    """Re-wrapping a signature is the most ordinary thing a model does, and
    refusing it as drift would be a false alarm."""
    rewrapped = DECLARED.replace(" : ", " :\n    ").replace(", ", ",\n  ")
    assert rewrapped != DECLARED

    result = run(proving.try_proof(declared, rewrapped, "by simp", accepts))
    assert result.get("error") is None, result.get("message", "")


def test_nothing_declared_means_nothing_to_drift_from(tmp_path):
    """The condition that keeps this safe. With no statement check on record
    `declared_goal` is empty and nothing can score against it anyway, so a run
    configured not to check statements must be unaffected rather than unable
    to attempt anything at all."""
    workdir = str(tmp_path)
    log.clear(workdir)
    assert log.declared_goal(workdir) == ""

    result = run(proving.try_proof(workdir, DRIFTED, "by simp", accepts))
    assert result.get("error") is None, result.get("message", "")


def test_a_later_check_moves_the_goal(declared):
    """Complying works, and is the intended route for a better formalisation:
    check it, and then proving it counts."""
    run(proving.check_statement(declared, DRIFTED, accepts))

    result = run(proving.try_proof(declared, DRIFTED, "by simp", accepts))
    assert result.get("error") is None, result.get("message", "")

    # ...and the one that used to be the goal is now the drift.
    result = run(proving.try_proof(declared, DECLARED, "by simp", accepts))
    assert result["error"] == "not_the_goal"


def test_the_weakening_attack_is_still_refused(declared):
    """`alg-square-nonneg` really submitted this shape: a trivially true
    weakening of the declared theorem. It is refused by the same rule that
    refuses an honest reformulation, which is the point -- the guard does not
    need to tell them apart."""
    weakened = "theorem sum_odd_eq_sq (n : ℕ) : n * n ≥ n - n"

    result = run(proving.try_proof(declared, weakened, "by simp", accepts))
    assert result["error"] == "not_the_goal"
    assert not log.records(declared, log.PROOF)


def test_drift_outranks_the_repeat_diagnosis(declared):
    """Ordering. There is no point telling the model it repeated an attempt
    at something that could never have counted."""
    run(proving.try_proof(declared, DRIFTED, "by simp", accepts))
    result = run(proving.try_proof(declared, DRIFTED, "by simp", accepts))
    assert result["error"] == "not_the_goal", result.get("error")
