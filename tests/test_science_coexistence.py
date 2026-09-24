"""The science verifiers must not disturb the mathematics (Phase 5).

Adding six kinds and seven tools to a system whose whole value is its
soundness is the moment to check that nothing moved. These tests assert the
seams rather than the features: that routing is unambiguous, that the maths
verifiers still own every maths kind, that the guard's rules are unchanged,
and that theorem proving is untouched.
"""

import pytest

import verifiers
from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from pipeline import faithfulness
from pipeline.tools import VerificationLog, make_tools

MATHS_KINDS = (
    VerificationKind.EQUALITY,
    VerificationKind.NUMERIC,
    VerificationKind.PRIMALITY,
    VerificationKind.SOLUTION,
    VerificationKind.LIMIT,
    VerificationKind.SERIES,
    VerificationKind.MATRIX,
    VerificationKind.INEQUALITY,
    VerificationKind.FACTORIZATION,
)

SCIENCE_KINDS = (
    VerificationKind.DIMENSION,
    VerificationKind.QUANTITY,
    VerificationKind.MOLAR_MASS,
    VerificationKind.CONSTANT,
    VerificationKind.PLAUSIBILITY,
    VerificationKind.BALANCE,
    VerificationKind.STATISTIC,
)


# -------------------------------------------------------------------- routing
@pytest.mark.parametrize("kind", MATHS_KINDS + SCIENCE_KINDS)
def test_exactly_one_verifier_claims_each_kind(kind):
    """Two verifiers claiming one kind means the verdict depends on list
    order, which is a silent way to change behaviour by reordering imports."""
    request = VerificationRequest(kind=kind, lhs="1", rhs="1")
    owners = [v.name for v in verifiers.VERIFIERS if v.supports(request)]
    assert len(owners) == 1, f"{kind.value} is claimed by {owners}"


@pytest.mark.parametrize("kind", MATHS_KINDS)
def test_the_maths_kinds_still_belong_to_sympy(kind):
    request = VerificationRequest(kind=kind, lhs="1", rhs="1")
    owners = [v.name for v in verifiers.VERIFIERS if v.supports(request)]
    assert owners == ["sympy"]


def test_the_formal_kind_still_belongs_to_lean():
    request = VerificationRequest(kind=VerificationKind.FORMAL, statement="x")
    owners = [v.name for v in verifiers.VERIFIERS if v.supports(request)]
    assert owners == ["lean"]


def test_an_undecidable_kind_still_falls_through_to_not_applicable():
    verdict = verifiers.verify(VerificationRequest(kind=VerificationKind.NONE))
    assert verdict.status is VerificationStatus.NOT_APPLICABLE


# ---------------------------------------------------------------- the maths
def test_the_original_maths_checks_are_unchanged():
    """A spot check across the kinds the project shipped with."""
    def decide(**kwargs):
        return verifiers.verify(VerificationRequest(**kwargs)).status

    assert decide(
        kind=VerificationKind.PRIMALITY, lhs="97"
    ) is VerificationStatus.TRUE
    assert decide(
        kind=VerificationKind.PRIMALITY, lhs="91"
    ) is VerificationStatus.FALSE
    assert decide(
        kind=VerificationKind.EQUALITY, lhs="diff(x**3, x)", rhs="3*x**2"
    ) is VerificationStatus.TRUE
    assert decide(
        kind=VerificationKind.NUMERIC, lhs="2+2", rhs="5"
    ) is VerificationStatus.FALSE


def test_a_bare_number_claim_is_not_hijacked_by_the_units_verifier():
    """The units verifier hands back anything with no unit in it, so that
    arithmetic keeps going to the engine that decides arithmetic."""
    verdict = verifiers.verify(
        VerificationRequest(kind=VerificationKind.QUANTITY, lhs="2+2", rhs="4")
    )
    assert verdict.status is VerificationStatus.UNKNOWN
    assert verdict.method == "units"


# ----------------------------------------------------------------- the tools
def test_every_tool_is_callable_and_records_exactly_one_check():
    """The guard computes the verdict from recorded executions. A tool that
    forgets to record is invisible to it."""
    log = VerificationLog()
    tools = make_tools(log)
    by_name = {tool.__name__: tool for tool in tools}

    calls = {
        "check_primality": ("a claim", "7"),
        "check_dimensions": ("a claim", "joule", "newton"),
        "check_quantity": ("a claim", "1000*meter", "1*kilometer"),
        "check_molar_mass": ("a claim", "H2O", "18.015"),
        "check_constant": ("a claim", "N_A", "6.022e23"),
        "check_possible": ("a claim", "probability", "1.4"),
        "check_equation_balances": ("a claim", "2H2 + O2 -> 2H2O"),
        "check_statistic": ("a claim", "binomial probability", "n=2,p=0.5,k=1", "0.5"),
    }
    for name, arguments in calls.items():
        before = len(log.checks)
        by_name[name](*arguments)
        assert len(log.checks) == before + 1, f"{name} recorded nothing"
        assert log.checks[-1].tool == name


def test_the_tool_list_has_no_duplicate_names():
    names = [tool.__name__ for tool in make_tools(VerificationLog())]
    assert len(set(names)) == len(names)


def test_every_tool_has_a_docstring_because_it_is_the_models_instructions():
    for tool in make_tools(VerificationLog()):
        assert (tool.__doc__ or "").strip(), f"{tool.__name__} has no docstring"


# ----------------------------------------------------------- faithfulness lint
def test_a_claimed_physical_value_is_linted():
    """Silent correction is the damaging failure: the user states a wrong
    value, the model checks the right one, every component behaves, and the
    answer addresses a question nobody asked. Half the science golden set
    exists to provoke exactly this."""
    question = "An object falls for 3 seconds with g = 9.8. Is the distance 29.4?"
    corrected = VerificationRequest(
        kind=VerificationKind.QUANTITY,
        lhs="0.5*9.8*(meter/second**2)*(3*second)**2",
        rhs="44.1*meter",
    )
    assert not faithfulness.is_faithful(question, corrected)

    as_asked = VerificationRequest(
        kind=VerificationKind.QUANTITY,
        lhs="0.5*9.8*(meter/second**2)*(3*second)**2",
        rhs="29.4*meter",
    )
    assert faithfulness.is_faithful(question, as_asked)


def test_a_looked_up_value_is_not_linted():
    """Avogadro's number is not expected to appear in the question that
    needs it. Linting a lookup would make every correct use of the
    constants table read as unfaithful."""
    question = "How many atoms are in 2 moles?"
    lookup = VerificationRequest(
        kind=VerificationKind.CONSTANT, lhs="N_A", rhs="6.02214076e23"
    )
    assert faithfulness.is_faithful(question, lookup)


def test_a_sanity_check_is_not_linted():
    """The value being sanity-checked was computed by the agent. It is
    supposed not to be in the question; that is the point of the check."""
    question = "What is the efficiency of an engine taking 100 J and doing 40 J?"
    sanity = VerificationRequest(
        kind=VerificationKind.PLAUSIBILITY, lhs="efficiency", rhs="0.4"
    )
    assert faithfulness.is_faithful(question, sanity)


# --------------------------------------------------------------- the proving
def test_the_proving_path_does_not_import_the_science_verifiers():
    """Theorem proving must be untouched by this work. If it never imports
    them, it cannot have been changed by them."""
    import pipeline.prover as prover

    source = open(prover.__file__, encoding="utf-8").read()
    for name in ("units_verifier", "reference_verifier", "statistics_verifier",
                 "plausibility_verifier", "chemistry_verifier"):
        assert name not in source


def test_the_verification_request_gained_only_optional_fields():
    """Every existing caller builds a request without the new fields."""
    request = VerificationRequest(kind=VerificationKind.PRIMALITY, lhs="7")
    assert request.tolerance == ""
    assert request.parameters == ""
