"""Offline tests for the statement pre-flight. No model, no Lean, no network.

From `lin-vector-space-basis`: the formalizer wrote `Basis`, which current
Mathlib calls `Module.Basis`, so the statement could never compile — and the
run still reported a formalisation rate of 100%.

`test_an_unelaborable_statement_is_a_formalisation_failure` is the one that
matters. Scoring it as "not proved" blames the prover for a proof that could
never have existed.
"""

import pytest

import config
from domain.proof import ProofRun
from eval.proof_metrics import ProofOutcome, classify
from pipeline.statement import (
    elaboration_errors,
    ensure_elaborates,
    name_hints,
    unknown_identifiers,
)
from verifiers.lean_runner import LeanOutcome, LeanResult

BASIS_ERROR = (
    "Claim.lean:4:28: error: Function expected at\n  Basis\n"
    "Hint: The identifier `Basis` is unknown, and Lean's autoImplicit option"
)


class Formalizer:
    """Records the repair request and returns whatever it was told to."""

    def __init__(self, repaired=""):
        self._repaired = repaired
        self.asked = None

    def repair_statement(self, goal, statement, errors, hints=""):
        self.asked = {"goal": goal, "statement": statement,
                      "errors": errors, "hints": hints}
        return self._repaired


class Search:
    def __init__(self, suggestions=()):
        self.queries = []
        self._suggestions = list(suggestions)

    def search_with_suggestions(self, query, limit=None):
        self.queries.append(query)
        return [], list(self._suggestions)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "CHECK_STATEMENT", True)


def run_for(statement="theorem t : True"):
    return ProofRun(goal="a claim", statement=statement)


def ok(_statement):
    return ""


def broken(_statement):
    return BASIS_ERROR


# ------------------------------------------------------------- parsing
def test_both_of_leans_phrasings_for_an_unknown_name_are_recognised():
    assert unknown_identifiers(BASIS_ERROR) == ["Basis"]
    assert unknown_identifiers("unknown identifier 'foo.bar'") == ["foo.bar"]


def test_a_repeated_name_is_only_reported_once():
    assert unknown_identifiers(BASIS_ERROR + "\n" + BASIS_ERROR) == ["Basis"]


# ------------------------------------------------------- elaboration check
def test_the_statement_is_checked_with_sorry_so_only_the_signature_is_tested():
    seen = {}

    def runner(source):
        seen["source"] = source
        return LeanResult(LeanOutcome.INCOMPLETE)

    assert elaboration_errors("theorem t : True", runner) == ""
    assert "sorry" in seen["source"]


def test_a_missing_lean_is_not_read_as_a_broken_statement():
    """Unknown is not broken. Refusing to prove without a compiler is absurd."""
    runner = lambda _s: LeanResult(LeanOutcome.UNAVAILABLE)
    assert elaboration_errors("theorem t : True", runner) == ""


def test_a_rejected_statement_returns_the_compiler_errors():
    runner = lambda _s: LeanResult(LeanOutcome.ERRORS, BASIS_ERROR)
    assert "Basis" in elaboration_errors("theorem t : Basis", runner)


# ------------------------------------------------------------- the hints
def test_loogle_is_asked_what_the_unknown_name_was_renamed_to():
    """Verified against the live service: ?q=Basis suggests Module.Basis."""
    search = Search(suggestions=['"Basis"', "Module.Basis", "PowerBasis.basis"])
    hints = name_hints(BASIS_ERROR, search)

    assert search.queries == ["Basis"]
    assert "Module.Basis" in hints
    assert '"Basis"' not in hints, "the quoted substring form is not a name"


def test_retrieval_failing_does_not_break_the_repair():
    class Broken:
        def search_with_suggestions(self, query, limit=None):
            raise RuntimeError("network gone")

    assert name_hints(BASIS_ERROR, Broken()) == ""


# --------------------------------------------------------------- the flow
def test_a_statement_that_elaborates_is_left_alone():
    formalizer = Formalizer()
    run = run_for()

    assert ensure_elaborates(run, "a claim", formalizer, checker=ok)
    assert formalizer.asked is None, "a working statement was sent for repair"
    assert run.telemetry.lean_calls == 1


def test_a_broken_statement_is_repaired_and_rechecked():
    formalizer = Formalizer(repaired="theorem t : Module.Basis")
    run = run_for("theorem t : Basis")
    checks = iter([BASIS_ERROR, ""])          # broken, then fixed

    assert ensure_elaborates(run, "a claim", formalizer,
                             checker=lambda _s: next(checks))
    assert run.statement == "theorem t : Module.Basis"
    assert run.statement_ok
    assert any("repaired" in entry for entry in run.trace), (
        "a rewritten statement must be visible to a human"
    )


def test_the_repair_is_told_what_lean_said():
    formalizer = Formalizer(repaired="theorem t : Module.Basis")
    checks = iter([BASIS_ERROR, ""])
    ensure_elaborates(run_for("theorem t : Basis"), "a claim", formalizer,
                      search=Search(suggestions=["Module.Basis"]),
                      checker=lambda _s: next(checks))

    assert "Basis" in formalizer.asked["errors"]
    assert "Module.Basis" in formalizer.asked["hints"]


def test_a_repair_that_still_does_not_elaborate_gives_up():
    run = run_for("theorem t : Basis")
    assert not ensure_elaborates(run, "a claim",
                                 Formalizer(repaired="theorem t : StillWrong"),
                                 checker=broken)
    assert not run.statement_ok


def test_only_one_repair_is_attempted():
    """Otherwise a confidently wrong formalizer loops on the model's budget."""
    formalizer = Formalizer(repaired="theorem t : StillWrong")
    calls = []

    def checker(statement):
        calls.append(statement)
        return BASIS_ERROR

    ensure_elaborates(run_for("theorem t : Basis"), "a claim", formalizer,
                      checker=checker)
    assert len(calls) == 2, "the repair loop ran more than once"


def test_a_formalizer_that_raises_does_not_take_the_run_down():
    class Exploding:
        def repair_statement(self, *a, **k):
            raise RuntimeError("quota")

    run = run_for("theorem t : Basis")
    assert not ensure_elaborates(run, "a claim", Exploding(), checker=broken)
    assert not run.statement_ok


def test_the_check_can_be_turned_off_for_an_ablation(monkeypatch):
    monkeypatch.setattr(config, "CHECK_STATEMENT", False)
    run = run_for()
    assert ensure_elaborates(run, "a claim", Formalizer(), checker=broken)
    assert run.telemetry.lean_calls == 0


# ------------------------------------------------------------- the metric
def test_an_unelaborable_statement_is_a_formalisation_failure():
    """THE point. It is not the prover's failure, and must not be counted as one."""
    run = ProofRun(goal="g", statement="theorem t : Basis", statement_ok=False)
    assert classify(run) is ProofOutcome.NOT_FORMALIZED


def test_a_good_statement_with_no_proof_is_still_a_proving_failure():
    run = ProofRun(goal="g", statement="theorem t : True")
    assert classify(run) is ProofOutcome.NOT_PROVED
