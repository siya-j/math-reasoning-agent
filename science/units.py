"""Reading unit expressions, in one place.

MOVED DOWN HERE so that two callers can share it. The units verifier owned
this, and the uncertainty machinery now needs the same namespace to
propagate error bars through quantities that carry units. `science/` cannot
import `verifiers/` -- verifiers/reference_verifier.py already imports
`science`, so the dependency would be a cycle -- and a second copy of a unit
list would drift, which is the reasoning pipeline/faithfulness.py gives for
keeping `unsupported_in` in a single place.

SECURITY. These strings come from a language model and SymPy's parser
evaluates what it reads, so it gets an explicit allow-list rather than a
live namespace.
"""

from __future__ import annotations

import re

import sympy
from sympy.parsing.sympy_parser import parse_expr, standard_transformations
from sympy.physics import units as physical_units

MATH_NAMES = (
    "sqrt exp log ln sin cos tan asin acos atan pi E Abs Rational Integer "
    "Float floor ceiling Mul Add Pow"
).split()

UNIT_NAMES = (
    "meter meters metre metres kilogram kilograms gram grams "
    "second seconds minute minutes hour hours day days "
    "ampere amperes kelvin kelvins mole moles candela "
    "newton newtons joule joules watt watts pascal pascals "
    "coulomb coulombs volt volts ohm ohms farad henry hertz "
    "liter liters litre litres milliliter milliliters "
    "centimeter centimeters millimeter millimeters kilometer kilometers "
    "micrometer nanometer picometer angstrom "
    "milligram milligrams microgram nanogram tonne "
    "electronvolt electronvolts atmosphere atmospheres bar torr "
    "degree radian steradian "
    "speed_of_light planck boltzmann avogadro_number gravitational_constant "
    "elementary_charge electron_rest_mass acceleration_due_to_gravity "
    "molar_gas_constant atomic_mass_constant"
).split()

KNOWN_UNITS = frozenset(
    name for name in UNIT_NAMES if getattr(physical_units, name, None) is not None
)

_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def namespace(extra: dict | None = None) -> dict:
    """The allow-listed names a unit expression may use."""
    space = {name: getattr(sympy, name) for name in MATH_NAMES
             if hasattr(sympy, name)}
    for name in UNIT_NAMES:
        unit = getattr(physical_units, name, None)
        if unit is not None:
            space[name] = unit
    if extra:
        space.update(extra)
    return space


def parse(text: str, extra: dict | None = None, evaluate: bool = True):
    """Parse a unit-bearing expression in the restricted namespace."""
    if not (text or "").strip():
        raise ValueError("empty expression")
    return parse_expr(
        text,
        local_dict=namespace(extra),
        global_dict={},
        transformations=standard_transformations,
        evaluate=evaluate,
    )


def mentions_units(text: str) -> bool:
    """Does the WRITTEN expression name any unit?

    Deliberately not an inspection of the SIMPLIFIED expression. A quantity
    that cancels to dimensionless has no units left to find, so asking the
    simplified form whether units were involved answers "no" for exactly the
    expressions a scientist most wants checked -- which is how "is this
    group dimensionless?" came to be refused outright.
    """
    return any(word in KNOWN_UNITS for word in _WORD.findall(text or ""))


def units_in(expression) -> set:
    return set(expression.atoms(physical_units.Quantity))


def strip_units(expression):
    """The pure number left once units are divided out, or None."""
    value = sympy.simplify(expression)
    for unit in units_in(value):
        value = value.subs(unit, 1)
    value = sympy.N(sympy.simplify(value))
    return float(value) if value.is_number and value.is_real else None


def incoherent_sum(expression) -> str:
    """Describe an addition of unlike dimensions, or "" if there is none.

    NEEDED BECAUSE `strip_units` CANNOT SEE ONE. It divides each unit out by
    substituting 1 for it, so `1 metre + 2 seconds` collapses to the number
    3 and reports no trouble at all. A propagated uncertainty built on that
    would be a number with no meaning wearing the shape of one with meaning.

    Same rule as the units verifier applies to a quantity: terms of an Add
    must share a dimension.
    """
    from sympy.physics.units.systems.si import SI

    system = SI.get_dimension_system()

    def base(term) -> dict:
        dependencies = system.get_dimensional_dependencies(
            SI.get_dimensional_expr(term)
        )
        return {str(getattr(k, "name", k)): v for k, v in dependencies.items() if v}

    for total in expression.atoms(sympy.Add):
        try:
            seen = [base(term) for term in total.args]
        except Exception:
            continue
        if any(d != seen[0] for d in seen[1:]):
            shown = sorted({
                " * ".join(f"{n}**{p}" if p != 1 else n for n, p in sorted(d.items()))
                or "dimensionless"
                for d in seen
            })
            return f"'{total}' adds " + " and ".join(shown)
    return ""
