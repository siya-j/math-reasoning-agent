"""Offline tests for statement-preservation review.

The load-bearing property is negative: **the reviewer can only refuse.**
`test_the_reviewer_cannot_turn_a_failure_into_a_proof` is the one that must
never be deleted — a reviewer able to grant approval is a new way to be
confidently wrong, which is the failure this architecture exists to prevent.
"""

from domain.proof import ProofRun
from domain.verdict import Verdict, VerificationStatus as S
from llm.reviewer import Review, Reviewer, parse_review
from pipeline.prover import prove

MATCHES = "VERDICT: matches\nCONCERN: none"
DIFFERS = (
    "VERDICT: differs\n"
    "CONCERN: the question asked whether 2 is the only solution, the statement "
    "asserts only that 2 is a solution"
)

ACCEPTED = Verdict(S.TRUE, "lean", "accepted")
REJECTED = Verdict(S.UNKNOWN, "lean", "error: nope")


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


class Formalizer:
    _skeleton = "by trivial"

    def statement(self, goal):
        return "theorem t : True"

    def sketch(self, goal):
        return "sketch"

    def proof(self, statement, sketch, errors="", previous=""):
        return "by trivial"

    def skeleton(self, statement, sketch, count=4):
        return self._skeleton

    def hole(self, claim, context, statement=""):
        self.holes_filled = getattr(self, "holes_filled", 0) + 1
        return "trivial"

    def lemmas(self, goal, count):
        return []

    def synthesis(self, statement, lemmas):
        return "by trivial"


def accepts(statement, proof):
    return ACCEPTED


def rejects(statement, proof):
    return REJECTED


# ----------------------------------------------------------------- parsing
def test_a_matching_statement_raises_no_concern():
    review = parse_review(MATCHES)
    assert review.performed
    assert not review.objected


def test_a_differing_statement_records_the_concern():
    review = parse_review(DIFFERS)
    assert review.objected
    assert "only solution" in review.concerns[0]


def test_a_mismatch_without_a_reason_still_counts_as_a_concern():
    review = parse_review("VERDICT: differs\nCONCERN: none")
    assert review.objected


def test_unparseable_output_raises_no_concern():
    """Fail open: an unreadable reviewer must not block a compiled proof."""
    assert not parse_review("I'm not sure what you mean").objected


def test_an_unreachable_model_records_that_review_did_not_happen():
    review = Reviewer(model=ExplodingModel()).review("q", "theorem t : True")
    assert not review.performed
    assert not review.objected
    assert "not reviewed" in review.note()


def test_review_is_skipped_when_there_is_nothing_to_compare():
    assert not Reviewer(model=FakeModel(DIFFERS)).review("", "theorem t").performed


# ------------------------------------------------- it can ONLY downgrade
def test_the_reviewer_cannot_turn_a_failure_into_a_proof():
    """THE constraint. A reviewer that can approve is a way to be wrong.

    AI Co-Mathematician (2605.06651): optimising against a reviewer can
    converge to arguments whose flaws that reviewer can no longer detect.
    """
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=rejects,               # the compiler refused
        depth=0,
        reviewer=Reviewer(model=FakeModel(MATCHES)),   # reviewer is happy
    )
    assert not run.proved
    assert run.verdict.status is S.UNKNOWN


def test_an_objection_downgrades_a_compiled_proof():
    run = prove(
        "Is 2 the only solution of x^2 = 4?",
        formalizer=Formalizer(),
        check=accepts,
        depth=0,
        reviewer=Reviewer(model=FakeModel(DIFFERS)),
    )
    assert run.verdict.status is S.UNKNOWN
    assert run.verdict.method == "reviewer"
    assert "may not match the question" in run.verdict.detail


def test_the_proof_is_kept_even_when_review_objects():
    """The artefact is still real; only the claim about it is withdrawn."""
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,
        depth=0,
        reviewer=Reviewer(model=FakeModel(DIFFERS)),
    )
    assert run.proof, "the artefact was discarded along with the claim"


def test_no_objection_leaves_the_verdict_alone():
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,
        depth=0,
        reviewer=Reviewer(model=FakeModel(MATCHES)),
    )
    assert run.proved
    assert run.verdict.status is S.TRUE


def test_without_a_reviewer_nothing_changes():
    run = prove("a claim", formalizer=Formalizer(), check=accepts, depth=0)
    assert run.proved
    assert run.review is None


# --------------------------------------------------------------- disclosure
def test_the_report_says_whether_review_happened():
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,
        depth=0,
        reviewer=Reviewer(model=ExplodingModel()),
    )
    assert "not reviewed" in run.report()


def test_the_report_shows_an_objection():
    run = prove(
        "a claim",
        formalizer=Formalizer(),
        check=accepts,
        depth=0,
        reviewer=Reviewer(model=FakeModel(DIFFERS)),
    )
    assert "review objected" in run.report()


def test_the_reviewer_is_asked_about_the_statement_not_the_proof():
    """Its job is translation fidelity, not mathematics."""
    model = FakeModel(MATCHES)
    Reviewer(model=model).review("Is 561 prime?", "theorem t : Nat.Prime 561")

    prompt = model.prompts[0]
    assert "Is 561 prime?" in prompt
    assert "theorem t : Nat.Prime 561" in prompt
    assert "Do not judge whether either is true" in prompt


def test_a_review_has_no_approved_field():
    """Structural: there is no way to express approval."""
    assert not hasattr(Review(), "approved")
