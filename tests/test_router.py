"""Offline tests for interpretation, classification and routing (Doc §10).

No model, no SymPy, no Lean. Both engines are injected, because what is
tested here is the ROUTING DECISION — which engine runs, in what order, and
what happens when the classification is wrong.

The classification comes from a model and will sometimes be wrong. The
contract is that a wrong route costs an attempt, never an answer.
"""

from domain.claim import Claim, ProblemType
from domain.proof import ProofRun
from domain.state import AgentRun
from domain.verdict import Verdict, VerificationStatus as S
from llm.interpreter import Interpreter, parse_classification
from pipeline.router import ask

COMPUTATIONAL = """TYPE: computational
CLAIM: 561 is a prime number
WHY: primality of a concrete integer is a computation"""

FORMAL = """TYPE: formal
CLAIM: every group of prime order is cyclic
WHY: a general statement about all groups, which needs a proof"""


class FakeModel:
    def __init__(self, reply):
        self._reply = reply
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("Reply", (), {"text": self._reply})()


class ExplodingModel:
    def invoke(self, prompt):
        raise RuntimeError("provider down")


def interpreter(reply):
    return Interpreter(model=FakeModel(reply))


def verification(status):
    run = AgentRun(question="q")
    run.verdict = Verdict(status, "sympy", "detail")
    run.answer = f"[{status.value}]"
    return lambda question: run


def proof(proved):
    def run(question, **kwargs):
        result = ProofRun(goal=question)
        result.verdict = Verdict(
            S.TRUE if proved else S.UNKNOWN, "lean", "detail"
        )
        result.proof = "by trivial" if proved else ""
        return result

    return run


def exploding(question, **kwargs):
    raise RuntimeError("engine crashed")


# ------------------------------------------------------------- parsing
def test_a_computational_classification_is_read():
    claim = parse_classification(COMPUTATIONAL, "Is 561 prime?")
    assert claim.problem_type is ProblemType.COMPUTATIONAL
    assert claim.statement == "561 is a prime number"
    assert "computation" in claim.reason


def test_a_formal_classification_is_read():
    claim = parse_classification(FORMAL, "groups?")
    assert claim.problem_type is ProblemType.FORMAL


def test_unparseable_output_defaults_to_computational():
    """The cheap deterministic engine, not a proof budget spent on 2 + 2."""
    claim = parse_classification("I'm not sure what you mean!", "Is 2+2=4?")
    assert claim.problem_type is ProblemType.COMPUTATIONAL


def test_the_original_question_is_always_kept():
    claim = parse_classification(COMPUTATIONAL, "Is 561 a prime number?")
    assert claim.question == "Is 561 a prime number?"


def test_claim_text_falls_back_to_the_question():
    assert Claim(question="original", statement="").text == "original"


def test_a_model_failure_still_produces_a_usable_claim():
    """Classification is a convenience; losing it must not end the run."""
    claim = Interpreter(model=ExplodingModel()).interpret("Is 2 + 2 = 4?")
    assert claim.problem_type is ProblemType.COMPUTATIONAL
    assert "unavailable" in claim.reason


# ------------------------------------------------------------- routing
def test_a_computational_claim_goes_to_sympy_first():
    answer = ask(
        "Is 561 prime?",
        interpreter=interpreter(COMPUTATIONAL),
        verify=verification(S.FALSE),
        prove=proof(proved=False),
    )
    assert answer.verification is not None
    assert answer.proof is None, "should not have needed the prover"
    assert answer.settled


def test_a_formal_claim_goes_to_lean_first():
    answer = ask(
        "Every group of prime order is cyclic",
        interpreter=interpreter(FORMAL),
        verify=verification(S.NOT_APPLICABLE),
        prove=proof(proved=True),
    )
    assert answer.proof is not None
    assert answer.verification is None, "should not have needed SymPy"
    assert answer.settled


# ------------------------------------------------- misrouting is recoverable
def test_a_computational_claim_misrouted_as_formal_still_gets_verified():
    """The prover finds nothing, so SymPy is tried anyway."""
    answer = ask(
        "Is 561 prime?",
        interpreter=interpreter(FORMAL),          # wrong classification
        verify=verification(S.FALSE),
        prove=proof(proved=False),
    )
    assert answer.proof is not None, "prover was tried first"
    assert answer.verification is not None, "no fallback happened"
    assert answer.settled
    assert any("fallback" in entry for entry in answer.trace)


def test_a_formal_claim_misrouted_as_computational_still_reaches_lean():
    answer = ask(
        "Every group of prime order is cyclic",
        interpreter=interpreter(COMPUTATIONAL),   # wrong classification
        verify=verification(S.NOT_APPLICABLE),
        prove=proof(proved=True),
    )
    assert answer.verification is not None
    assert answer.proof is not None, "no fallback to the prover"
    assert answer.settled


def test_a_settled_claim_never_triggers_a_fallback():
    """Fallback costs a model budget; it must not run when unnecessary."""
    answer = ask(
        "Is 2 + 2 = 4?",
        interpreter=interpreter(COMPUTATIONAL),
        verify=verification(S.TRUE),
        prove=exploding,          # would raise if called
    )
    assert answer.settled
    assert answer.proof is None


def test_nothing_settled_is_reported_honestly():
    answer = ask(
        "Is this a good theorem?",
        interpreter=interpreter(COMPUTATIONAL),
        verify=verification(S.NOT_APPLICABLE),
        prove=proof(proved=False),
    )
    assert not answer.settled


# ------------------------------------------------------------- robustness
def test_an_engine_crash_does_not_take_the_run_down():
    answer = ask(
        "Is 561 prime?",
        interpreter=interpreter(COMPUTATIONAL),
        verify=exploding,
        prove=proof(proved=False),
    )
    assert not answer.settled
    assert any("crashed" in entry for entry in answer.trace)


def test_an_unsupported_claim_is_still_offered_to_sympy():
    """Refusing to look is worse than looking and reporting NOT VERIFIED."""
    unsupported = "TYPE: unsupported\nCLAIM: is mathematics beautiful\nWHY: opinion"
    answer = ask(
        "Is mathematics beautiful?",
        interpreter=interpreter(unsupported),
        verify=verification(S.NOT_APPLICABLE),
        prove=exploding,          # must NOT be reached
    )
    assert answer.verification is not None
    assert answer.proof is None
    assert not answer.settled


# ------------------------------------------------------------- reporting
def test_the_report_states_how_the_question_was_routed():
    answer = ask(
        "Is 561 prime?",
        interpreter=interpreter(COMPUTATIONAL),
        verify=verification(S.FALSE),
        prove=proof(proved=False),
    )
    report = answer.report()
    assert "routed as: computational" in report
    assert "Is 561 prime?" in report
