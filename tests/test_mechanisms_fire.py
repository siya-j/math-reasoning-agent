"""Every automatic mechanism, driven with REAL REPL-format compiler output.

WHY THIS FILE EXISTS
--------------------
`_DIAGNOSTIC` required a file prefix the REPL backend does not emit, so
`LeanResult.errors` was empty on every REPL compile and `interpret` fell back
to the first line of output. That was not one broken feature. Measured against
the exact text `putnam_1962_a4` received on 38 of its 58 rejections:

    diagnosis.classify        -> 'unclassified'
    diagnosis.next_action     -> ''            (no "WHAT THIS MEANS")
    diagnosis.retrieval_query -> ''            (no automatic search)

Three mechanisms, silently inert, on the default backend. The third is the
sharpest loss: `_retrieve_for_failure` exists *because* the model's own
post-rejection queries were measured to be "constant", "deriv", "abs", "re" --
"meanwhile the error itself named exactly what was missing". The feature built
to fix bad searching never ran, and then bad searching was diagnosed as a
model failure.

WHAT THIS FILE CHECKS, AND WHY IT IS SHAPED THIS WAY
----------------------------------------------------
Not "is the logic right" -- other files do that with clean inputs. This asks
"does it fire AT ALL, given what the compiler really sends". Every test below
feeds output in the REPL's format, because that is the format that was silently
unparseable and it is the one every recent run used.

A mechanism that never fires cannot fail a test that never exercises it.
"""

import asyncio
import tempfile

import pytest

from math_v2.core import log, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

GOAL = "theorem mra_goal (G : Type*) [Group G] : IsCyclic G"

# Exactly how math_v2/tools/_repl.py:render formats a diagnostic: no file
# prefix. This is the shape that used to parse as nothing at all.
UNSOLVED = ("41:97: warning: Variable name `x` is not explicitly referenced.\n"
            "12:4: error: unsolved goals\ncase h\n⊢ IsCyclic G")
UNKNOWN_NAME = "3:2: error: unknown identifier 'Complex.abs'"
UNKNOWN_TACTIC = "2:3: error: unknown tactic"


def run(coro):
    return asyncio.run(coro)


def lean(*outcomes):
    """A compiler answering a scripted sequence, in REPL format."""
    seen = []

    async def runner(source):
        seen.append(source)
        outcome, output = outcomes[min(len(seen) - 1, len(outcomes) - 1)]
        return LeanResult(outcome, output)

    runner.seen = seen
    return runner


class Search:
    def __init__(self, found=()):
        from retrieval.loogle import Premise
        self.found = [Premise(name=n, type="∀ x, P x", module="Mathlib", doc="")
                      for n in found]
        self.queries = []

    def search_with_suggestions(self, query, limit=None):
        self.queries.append(query)
        return list(self.found), []

    def premises_for(self, statement):
        return list(self.found)


@pytest.fixture
def workdir():
    return tempfile.mkdtemp()


# =====================================================================
def test_a_rejection_carries_the_error_and_the_goal_state(workdir):
    """The base case everything else depends on. Before the fix this message
    contained a cosmetic warning and nothing else."""
    result = run(proving.try_proof(
        workdir, GOAL, "by simp", lean((LeanOutcome.ERRORS, UNSOLVED))))

    assert "unsolved goals" in result["message"]
    assert "⊢ IsCyclic G" in result["message"]


def test_the_failure_is_classified_rather_than_left_unknown(workdir):
    """`outputs.failure` drives nothing downstream today, but it is the
    published signal that the error was understood -- and it read
    'unclassified' on two thirds of a real run."""
    result = run(proving.try_proof(
        workdir, GOAL, "by simp", lean((LeanOutcome.ERRORS, UNSOLVED))))

    assert result["outputs"]["failure"] == "unsolved_goals"


def test_the_model_is_told_what_the_error_means(workdir):
    """The "WHAT THIS MEANS" instruction. `diagnosis` exists because the
    errors WERE being read and "the response to all of them was the same"; an
    empty instruction returns the agent to exactly that state."""
    result = run(proving.try_proof(
        workdir, GOAL, "by simp", lean((LeanOutcome.ERRORS, UNSOLVED))))

    assert "WHAT THIS MEANS" in result["message"]


def test_a_rejection_triggers_the_automatic_search_from_the_error(workdir):
    """THE mechanism whose silence cost the most. One Loogle query per
    rejected compile, built from what Lean said -- and it is a `|- ` shape
    query, which is what the model never once typed itself across 61
    searches."""
    search = Search(found=["isCyclic_of_prime_card"])

    result = run(proving.try_proof(
        workdir, GOAL, "by simp", lean((LeanOutcome.ERRORS, UNSOLVED)), search))

    assert search.queries, "no automatic search was made at all"
    assert search.queries[0].startswith("|- "), search.queries
    assert "isCyclic_of_prime_card" in result["message"]


def test_the_retrieved_premises_are_remembered_for_later_attempts(workdir):
    search = Search(found=["isCyclic_of_prime_card"])
    run(proving.try_proof(workdir, GOAL, "by simp",
                          lean((LeanOutcome.ERRORS, UNSOLVED)), search))

    names = [p["name"] for p in log.read(workdir)["premises"]]
    assert "isCyclic_of_prime_card" in names


def test_an_unknown_name_is_searched_as_a_fragment(workdir):
    """A different branch of `retrieval_query`, and it needs the error text
    just as much: the LAST dotted segment, quoted."""
    search = Search(found=["Complex.abs_apply"])

    run(proving.try_proof(workdir, GOAL, "exact Complex.abs x",
                          lean((LeanOutcome.ERRORS, UNKNOWN_NAME)), search))

    assert search.queries == ['"abs"'], search.queries


def test_the_automatic_exact_repair_fires(workdir):
    """A term written where a tactic was expected is repaired once, without a
    model call. Gated on `classify(...) is UNKNOWN_TACTIC`, so it was dead for
    the same reason everything else was."""
    compiler = lean((LeanOutcome.ERRORS, UNKNOWN_TACTIC),
                    (LeanOutcome.COMPILED, ""))

    result = run(proving.try_proof(workdir, GOAL, "isCyclic_of_prime_card h",
                                   compiler))

    assert len(compiler.seen) == 2, "the repair never ran"
    assert "exact isCyclic_of_prime_card h" in compiler.seen[1]
    assert result["outputs"]["accepted"] is True


def test_the_skeleton_filler_actually_attempts_the_holes(workdir):
    """`try_skeleton` acts on a decomposition rather than announcing it --
    added because "across four ProofNet goals it never did once"."""
    skeleton = "by\n  have hstep : (7 : Nat) * 6 = 42 := by sorry\n  sorry"

    async def compiler(source):
        if "sorry" in source:
            return LeanResult(LeanOutcome.ERRORS,
                              "0:0: warning: declaration uses 'sorry'")
        return LeanResult(LeanOutcome.COMPILED, "")

    result = run(proving.try_skeleton(workdir, GOAL, skeleton, compiler,
                                      fill_budget=4))

    assert result["outputs"]["typechecks"] is True
    assert result["outputs"]["lemmas_proved"], "no hole was ever attempted"


def test_the_statement_check_seeds_premises_from_the_goal_shape(workdir):
    """`seed_premises` runs the good query ladder once, the moment the
    statement is known to elaborate -- the agent's only premise source that
    does not depend on the model typing a good query."""
    search = Search(found=["isCyclic_of_prime_card"])

    run(proving.check_statement(
        workdir, GOAL,
        lean((LeanOutcome.ERRORS, "0:0: warning: declaration uses 'sorry'")),
        search))

    names = [p["name"] for p in log.read(workdir)["premises"]]
    assert "isCyclic_of_prime_card" in names


@pytest.mark.parametrize("error,expected", [
    ("unknown identifier 'Complex.abs'", '"abs"'),
    # A prime that BELONGS to the name. Lean writes it as a doubled quote, and
    # the delimiter is what tells the two apart.
    ("unknown identifier 'foo''", '"foo\'"'),
    ("unknown constant `Nat.succ_le`", '"succ_le"'),
    ("unknown identifier Complex.abs", '"abs"'),
])
def test_the_unknown_name_query_does_not_swallow_the_closing_quote(
        error, expected):
    """FOUND BY THIS AUDIT, and unreachable before it. `retrieval_query` is
    called only from `_retrieve_for_failure`, which needs a CLASSIFIED error,
    and no REPL diagnostic classified until `_DIAGNOSTIC` was fixed. So the
    mechanism was dead and its own bug could not be observed.

    A prime is legal inside a Lean identifier, so a character class containing
    `'` eats the closing quote of `'Complex.abs'` and the automatic search
    goes out as `"abs'"` -- which matches nothing. Reviving a mechanism into a
    broken query would have looked exactly like the mechanism still not
    working."""
    from math_v2.core import diagnosis

    assert diagnosis.retrieval_query(error) == expected
