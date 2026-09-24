"""Can the verifiers settle the science golden set AT ALL? (Phase 5)

This is a CEILING, measured offline and for free, and it is what makes the
eventual end-to-end number interpretable. Every decidable case in
eval/golden-science.json is paired here with the check an agent ought to
make, and the verifier is run directly.

If a case fails here, the verifiers cannot decide it and no prompting will
help. If a case passes here but fails in a live run, the gap is the model's
tool use — a different problem with a different fix. Without this table, a
disappointing run tells you only that something is wrong somewhere.

The repository already works this way: `test_metric_reachability.py` exists
for the same reason on the proving side.

These expectations are NOT a second copy of the golden set's answers. They
are derived from it: the case's own `expected` field is what each check is
asserted against, so the two cannot drift apart.
"""

import json
from pathlib import Path

import pytest

import verifiers
from domain.verdict import VerificationStatus
from domain.verification import VerificationKind, VerificationRequest

SCIENCE = Path(__file__).resolve().parents[1] / "eval" / "golden-science.json"

K = VerificationKind

# case id -> the request an agent ought to build for it.
#
# Written by hand, from the question text alone. Where a question states a
# value (g = 9.8), that value is used rather than the more precise constant,
# because the check must test the claim the question makes.
CHECKS: dict[str, VerificationRequest] = {
    # --------------------------------------------------------------- physics
    "phy-freefall-distance": VerificationRequest(
        kind=K.QUANTITY, lhs="0.5*9.8*(meter/second**2)*(3*second)**2",
        rhs="44.1*meter"),
    "phy-freefall-velocity-confused": VerificationRequest(
        kind=K.QUANTITY, lhs="0.5*9.8*(meter/second**2)*(3*second)**2",
        rhs="29.4*meter"),
    "phy-kinetic-energy": VerificationRequest(
        kind=K.QUANTITY, lhs="0.5*2*kilogram*(3*meter/second)**2", rhs="9*joule"),
    "phy-kinetic-energy-no-half": VerificationRequest(
        kind=K.QUANTITY, lhs="0.5*2*kilogram*(3*meter/second)**2", rhs="18*joule"),
    "phy-momentum": VerificationRequest(
        kind=K.QUANTITY, lhs="2*kilogram*3*meter/second",
        rhs="6*kilogram*meter/second"),
    "phy-newton-second": VerificationRequest(
        kind=K.QUANTITY, lhs="5*kilogram*2*meter/second**2", rhs="10*newton"),
    "phy-work-done": VerificationRequest(
        kind=K.QUANTITY, lhs="10*newton*3*meter", rhs="30*joule"),
    "phy-power": VerificationRequest(
        kind=K.QUANTITY, lhs="300*joule/(6*second)", rhs="50*watt"),
    "phy-power-wrong": VerificationRequest(
        kind=K.QUANTITY, lhs="300*joule/(6*second)", rhs="1800*watt"),
    "phy-ohms-law": VerificationRequest(
        kind=K.QUANTITY, lhs="2*ampere*5*ohm", rhs="10*volt"),
    "phy-resistors-parallel": VerificationRequest(
        kind=K.QUANTITY, lhs="1/(1/(6*ohm) + 1/(6*ohm))", rhs="3*ohm"),
    "phy-resistors-parallel-as-series": VerificationRequest(
        kind=K.QUANTITY, lhs="1/(1/(6*ohm) + 1/(6*ohm))", rhs="12*ohm"),
    "phy-photon-energy": VerificationRequest(
        kind=K.QUANTITY, lhs="6.626e-34*joule*second*5e14/second",
        rhs="3.313e-19*joule"),
    "phy-pendulum-period": VerificationRequest(
        kind=K.QUANTITY, lhs="2*pi*sqrt(1*meter/(9.8*meter/second**2))",
        rhs="2.0071*second", tolerance="4"),
    "phy-projectile-range": VerificationRequest(
        kind=K.QUANTITY,
        lhs="(20*meter/second)**2*sin(pi/2)/(9.8*meter/second**2)",
        rhs="40.816*meter", tolerance="3"),
    "phy-density": VerificationRequest(
        kind=K.QUANTITY, lhs="27*gram/(10*milliliter)", rhs="2.7*gram/milliliter"),
    "phy-relativity-gamma": VerificationRequest(
        kind=K.NUMERIC, lhs="1/sqrt(1 - 0.6**2)", rhs="1.25"),
    "phy-relativity-gamma-wrong": VerificationRequest(
        kind=K.NUMERIC, lhs="1/sqrt(1 - 0.6**2)", rhs="1.6"),
    "sci-na-boiling-point": None,
    "sci-na-model-choice": None,

    # ----------------------------------------------------------------- units
    "phy-units-joule": VerificationRequest(
        kind=K.DIMENSION, lhs="joule", rhs="kilogram*meter**2/second**2"),
    "phy-units-newton": VerificationRequest(
        kind=K.DIMENSION, lhs="newton", rhs="kilogram*meter/second**2"),
    "phy-units-add-mismatch": VerificationRequest(
        kind=K.DIMENSION, lhs="5*meter + 3*second", rhs="meter"),
    "phy-units-energy-vs-force": VerificationRequest(
        kind=K.DIMENSION, lhs="joule", rhs="newton"),
    "phy-units-ev-to-joule": VerificationRequest(
        kind=K.QUANTITY, lhs="electronvolt", rhs="1.602176634e-19*joule"),
    "chem-units-atm-to-pascal": VerificationRequest(
        kind=K.QUANTITY, lhs="atmosphere", rhs="101325*pascal"),
    "phy-units-speed-dimension": VerificationRequest(
        kind=K.DIMENSION, lhs="meter/second", rhs="kilometer/hour"),

    # ------------------------------------------------------------- chemistry
    "chem-molar-mass-water": VerificationRequest(
        kind=K.MOLAR_MASS, lhs="H2O", rhs="18.015"),
    "chem-molar-mass-water-wrong": VerificationRequest(
        kind=K.MOLAR_MASS, lhs="H2O", rhs="20.0"),
    "chem-molar-mass-co2": VerificationRequest(
        kind=K.MOLAR_MASS, lhs="CO2", rhs="44.009"),
    "chem-moles-from-mass": VerificationRequest(
        kind=K.NUMERIC, lhs="36.03/18.015", rhs="2"),
    "chem-molarity": VerificationRequest(
        kind=K.NUMERIC, lhs="0.5/2", rhs="0.25"),
    "chem-dilution": VerificationRequest(
        kind=K.NUMERIC, lhs="2*50/200", rhs="0.5"),
    "chem-dilution-wrong": VerificationRequest(
        kind=K.NUMERIC, lhs="2*50/200", rhs="0.8"),
    "chem-ideal-gas-molar-volume": VerificationRequest(
        kind=K.NUMERIC, lhs="1*0.08206*273.15", rhs="22.415", tolerance="3"),
    "chem-ph-strong-acid": VerificationRequest(
        kind=K.NUMERIC, lhs="-log(0.001)/log(10)", rhs="3"),
    "chem-ph-confused-with-poh": VerificationRequest(
        kind=K.NUMERIC, lhs="-log(0.001)/log(10)", rhs="11"),
    "chem-percent-yield": VerificationRequest(
        kind=K.NUMERIC, lhs="100*40/50", rhs="80"),
    "chem-avogadro-count": VerificationRequest(
        kind=K.NUMERIC, lhs="2*6.02214076e23", rhs="1.204428152e24"),
    "chem-stoich-water": VerificationRequest(
        kind=K.NUMERIC, lhs="4*2/2", rhs="4"),
    "chem-stoich-wrong-ratio": VerificationRequest(
        kind=K.NUMERIC, lhs="4*2/2", rhs="8"),
    "chem-limiting-reagent": VerificationRequest(
        kind=K.NUMERIC, lhs="4/2", rhs="2"),
    "chem-balance-correct": VerificationRequest(
        kind=K.BALANCE, lhs="2H2 + O2 -> 2H2O"),
    "chem-balance-incorrect": VerificationRequest(
        kind=K.BALANCE, lhs="H2 + O2 -> H2O"),
    "chem-heat-capacity": VerificationRequest(
        kind=K.QUANTITY, lhs="100*gram*4.18*joule/(gram*kelvin)*20*kelvin",
        rhs="8360*joule"),
    "sci-na-sn2-mechanism": None,
    "sci-na-spontaneity-no-data": None,

    # --------------------------------------------------------------- biology
    "bio-serial-dilution": VerificationRequest(
        kind=K.NUMERIC, lhs="10**3", rhs="1000"),
    "bio-serial-dilution-wrong": VerificationRequest(
        kind=K.NUMERIC, lhs="10**3", rhs="30"),
    "bio-hardy-weinberg-heterozygous": VerificationRequest(
        kind=K.NUMERIC, lhs="2*0.7*0.3", rhs="0.42"),
    "bio-hardy-weinberg-sums": VerificationRequest(
        kind=K.NUMERIC, lhs="0.7**2 + 2*0.7*0.3 + 0.3**2", rhs="1"),
    "bio-hardy-weinberg-q-wrong": VerificationRequest(
        kind=K.NUMERIC, lhs="1 - 0.7", rhs="0.5"),
    "bio-bacterial-doubling": VerificationRequest(
        kind=K.NUMERIC, lhs="1000*2**(120/20)", rhs="1000*2**6"),
    "bio-bacterial-doubling-linear": VerificationRequest(
        kind=K.NUMERIC, lhs="1000*2**(120/20)", rhs="6000"),
    "bio-percent-solution": VerificationRequest(
        kind=K.NUMERIC, lhs="100*5/100", rhs="5"),
    "bio-dosage-by-weight": VerificationRequest(
        kind=K.QUANTITY, lhs="5*milligram/kilogram*70*kilogram",
        rhs="350*milligram"),
    "bio-dosage-by-weight-wrong": VerificationRequest(
        kind=K.QUANTITY, lhs="5*milligram/kilogram*70*kilogram",
        rhs="75*milligram"),
    "bio-microscope-magnification": VerificationRequest(
        kind=K.NUMERIC, lhs="40*10", rhs="400"),
    "bio-monohybrid-ratio": VerificationRequest(
        kind=K.STATISTIC, lhs="binomial at least", parameters="n=1, p=0.75, k=1",
        rhs="0.75"),
    "bio-allele-frequency": VerificationRequest(
        kind=K.NUMERIC, lhs="sqrt(0.16)", rhs="0.4"),
    "sci-na-drug-safety": None,
    "sci-na-bird-evolution": None,
    "sci-na-enzyme-optimum": None,
}

CASES = {row["id"]: row for row in json.loads(SCIENCE.read_text())}


def test_every_case_has_an_entry_in_this_table():
    """A case added to the set without a line here would go unnoticed."""
    assert set(CHECKS) == set(CASES)


@pytest.mark.parametrize(
    "case_id", sorted(cid for cid, req in CHECKS.items() if req is not None)
)
def test_a_verifier_reaches_the_answer_the_golden_set_records(case_id):
    """The expectation comes from the case's own `expected` field, so this
    table and the golden set cannot drift apart."""
    expected = CASES[case_id]["expected"]
    verdict = verifiers.verify(CHECKS[case_id])
    assert verdict.status is VerificationStatus(expected), (
        f"{CASES[case_id]['question']}\n"
        f"  expected {expected}, verifier said {verdict.status.value}\n"
        f"  {verdict.detail}"
    )


@pytest.mark.parametrize(
    "case_id", sorted(cid for cid, req in CHECKS.items() if req is None)
)
def test_the_undecidable_cases_are_marked_undecidable(case_id):
    """These have no check because none exists. They measure restraint, and
    an entry here would mean the case was miscategorised."""
    assert CASES[case_id]["expected"] == "n/a"


def test_the_ceiling_is_total():
    """Stated as one number, because it is the thing to compare a live run
    against: every decidable case is reachable by some verifier."""
    decidable = [cid for cid, row in CASES.items() if row["expected"] != "n/a"]
    reachable = [
        cid for cid in decidable
        if CHECKS[cid] is not None
        and verifiers.verify(CHECKS[cid]).status is VerificationStatus(
            CASES[cid]["expected"]
        )
    ]
    assert len(reachable) == len(decidable), (
        f"only {len(reachable)} of {len(decidable)} decidable cases are "
        "reachable; the shortfall is a verifier gap, not a prompting problem"
    )


# ============================================================================
# THE CEILING ABOVE WAS MEASURED AT THE WRONG LAYER.
#
# Every check in the table is built as a VerificationRequest and handed
# straight to the verifiers. That tests what the verifiers CAN decide. It
# does not test what the model can ASK them, and those are different
# questions whenever a tool fails to expose a field the verifier reads.
#
# It cost a live run to find out. `tolerance` was added to the NUMERIC
# verifier in Phase 5, but `check_numeric` never took a `decimal_places`
# argument, so the rounding capability existed and was unreachable. Two
# cases that pass above came back as SOUNDNESS FAILURES on the real
# benchmark: 2*pi*sqrt(1/9.8) was refuted for being 2.007089 rather than
# exactly 2.0071.
#
# So the same table is run again THROUGH THE TOOLS, and the recorded request
# is compared against the intended one. A field the tool cannot carry now
# fails a test instead of a benchmark.
# ============================================================================

from pipeline.tools import VerificationLog, make_tools  # noqa: E402

# How each kind reaches a verifier from the model's side: the tool's name,
# and which request fields its arguments come from, in order.
TOOL_FOR = {
    K.NUMERIC: ("check_numeric", ("lhs", "rhs", "tolerance")),
    K.QUANTITY: ("check_quantity", ("lhs", "rhs", "tolerance")),
    K.DIMENSION: ("check_dimensions", ("lhs", "rhs")),
    K.MOLAR_MASS: ("check_molar_mass", ("lhs", "rhs")),
    K.CONSTANT: ("check_constant", ("lhs", "rhs")),
    K.PLAUSIBILITY: ("check_possible", ("lhs", "rhs")),
    K.BALANCE: ("check_equation_balances", ("lhs",)),
    K.STATISTIC: ("check_statistic", ("lhs", "parameters", "rhs")),
}


def through_tools(request: VerificationRequest):
    """Make this request the way the model would, and return what was recorded."""
    name, fields = TOOL_FOR[request.kind]
    log = VerificationLog()
    tool = {t.__name__: t for t in make_tools(log)}[name]
    tool("a claim", *(getattr(request, field) for field in fields))
    return log.checks[-1]


@pytest.mark.parametrize(
    "case_id", sorted(cid for cid, req in CHECKS.items() if req is not None)
)
def test_the_tool_layer_carries_every_field_the_check_needs(case_id):
    """A tool that drops a field silently verifies a different claim."""
    intended = CHECKS[case_id]
    recorded = through_tools(intended).request
    for field in ("lhs", "rhs", "tolerance", "parameters"):
        assert getattr(recorded, field) == getattr(intended, field), (
            f"{case_id}: the {field!r} field did not survive the tool layer. "
            f"The verifier can decide this; the model cannot ask it to."
        )


@pytest.mark.parametrize(
    "case_id", sorted(cid for cid, req in CHECKS.items() if req is not None)
)
def test_the_ceiling_holds_through_the_tools_too(case_id):
    expected = CASES[case_id]["expected"]
    verdict = through_tools(CHECKS[case_id]).verdict
    assert verdict.status is VerificationStatus(expected), (
        f"{CASES[case_id]['question']}\n"
        f"  expected {expected}, got {verdict.status.value} via the tool\n"
        f"  {verdict.detail}"
    )


def test_every_kind_the_table_uses_has_a_tool():
    """A verifier with no tool is a capability the model cannot reach."""
    used = {request.kind for request in CHECKS.values() if request is not None}
    assert used <= set(TOOL_FOR), f"no tool mapped for {used - set(TOOL_FOR)}"
