"""Units verifier — dimensional analysis and physical quantities (Phase 1).

WHY THIS EXISTS. The SymPy verifier decides numbers. A physics answer is not
a number: 44.1 is right and 44.1 seconds is wrong, and a verifier that only
sees the 44.1 cannot tell them apart. Worse, the most common error in a
physics calculation is not arithmetic but the assembly of the formula, and a
wrong assembly usually announces itself as a wrong DIMENSION long before it
announces itself as a wrong number. Checking dimensions catches mistakes that
checking arithmetic cannot see.

TWO KINDS, on purpose:

  DIMENSION   Do these two things have the same physical dimensions?
              Decides "is a joule a newton?" (no) without computing anything.

  QUANTITY    Does this physical expression equal this stated value?
              Converts to the claimed unit first, so an answer that is right
              in metres and claimed in feet is FALSE, not TRUE.

WHAT IT REFUSES. If neither side carries a unit, this is a plain arithmetic
claim and the SymPy verifier should have it; deciding it here would report
"dimensionally consistent" about something with no dimensions, which reads as
evidence and is not. If an expression adds unlike dimensions (5 metres plus
3 seconds) there is no value to rule on, but the claim that it is a
well-defined quantity is FALSE, and we say so.
"""

from __future__ import annotations

import sympy
from sympy.parsing.sympy_parser import parse_expr, standard_transformations
from sympy.physics import units as physical_units
from sympy.physics.units import convert_to
from sympy.physics.units.systems.si import SI

from domain.verdict import Verdict, VerificationStatus
from domain.verification import VerificationKind, VerificationRequest
from verifiers.base import Verifier

_SUPPORTED = {VerificationKind.DIMENSION, VerificationKind.QUANTITY}

_DIMENSION_SYSTEM = SI.get_dimension_system()

# SECURITY: identical reasoning to the SymPy verifier. These strings come from
# a language model and the parser evaluates what it reads, so it gets an
# explicit allow-list rather than a live namespace.
_MATH_NAMES = (
    "sqrt exp log ln sin cos tan asin acos atan pi E Abs Rational Integer "
    "Float floor ceiling Mul Add Pow"
).split()

_UNIT_NAMES = (
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


def _namespace() -> dict:
    space = {name: getattr(sympy, name) for name in _MATH_NAMES if hasattr(sympy, name)}
    for name in _UNIT_NAMES:
        unit = getattr(physical_units, name, None)
        if unit is not None:
            space[name] = unit
    return space


def _parse(text: str):
    if not text.strip():
        raise ValueError("empty expression")
    return parse_expr(
        text,
        local_dict=_namespace(),
        global_dict={},
        transformations=standard_transformations,
        evaluate=True,
    )


def _base_dimensions(expression) -> dict:
    """Reduce an expression to its exponents over the base dimensions.

    Needed because SymPy reports a joule as `energy` and kg*m**2/s**2 as
    `length**2*mass/time**2`. Those are the same dimension written two ways;
    only the base-dimension form compares equal.
    """
    dimensional = SI.get_dimensional_expr(expression)
    dependencies = _DIMENSION_SYSTEM.get_dimensional_dependencies(dimensional)
    # Normalise the keys to plain names: Dimension(mass) and Dimension(mass, M)
    # are the same dimension but do not always hash alike across versions.
    return {str(getattr(k, "name", k)): v for k, v in dependencies.items() if v}


def _units_in(expression) -> set:
    return set(expression.atoms(physical_units.Quantity))


def _describe(dimensions: dict) -> str:
    if not dimensions:
        return "dimensionless"
    parts = []
    for name in sorted(dimensions):
        power = dimensions[name]
        parts.append(name if power == 1 else f"{name}**{power}")
    return " * ".join(parts)


class _Incoherent(Exception):
    """An expression adds quantities of different dimensions."""


def _coherent(expression, source: str) -> dict:
    """Base dimensions of an expression, refusing incoherent sums.

    `5*meter + 3*second` has no dimensions. SymPy will happily carry it around
    as a sum, and every later step would then be answering a question about
    something that does not exist. Adding unlike quantities is the classic
    units error, so it is reported rather than swallowed.
    """
    for total in expression.atoms(sympy.Add):
        seen = [_base_dimensions(term) for term in total.args]
        if any(d != seen[0] for d in seen[1:]):
            shown = " and ".join(sorted({_describe(d) for d in seen}))
            raise _Incoherent(
                f"'{source}' adds quantities of different dimensions "
                f"({shown}), which is not a well-defined physical quantity."
            )
    return _base_dimensions(expression)


def _target_units(expression):
    """The units in the claimed value, to convert the other side into."""
    units = _units_in(expression)
    return list(units) if units else None


def _unit_text(target) -> str:
    return " ".join(str(unit) for unit in target) if target else ""


def _magnitude(expression):
    """The pure number left once units are divided out, or None."""
    value = sympy.simplify(expression)
    for unit in _units_in(value):
        value = value.subs(unit, 1)
    value = sympy.N(sympy.simplify(value))
    return float(value) if value.is_number and value.is_real else None


def _places(tolerance: str):
    text = (tolerance or "").strip()
    if not text:
        return None
    try:
        places = int(text)
    except ValueError:
        return None
    return places if places >= 0 else None


def _closeness(claimed) -> float:
    """How near two values must be to count as equal.

    Relative, because 1e-19 joules and 1e11 metres cannot share an absolute
    tolerance. Floating point alone costs a few units in the last place; this
    allows for that and nothing more, so a genuinely different answer still
    reads as FALSE.
    """
    size = _magnitude(claimed)
    if size is None:
        return 1e-9
    return max(abs(size) * 1e-9, 1e-12)


class UnitsVerifier(Verifier):
    name = "units"

    def supports(self, request: VerificationRequest) -> bool:
        return request.kind in _SUPPORTED

    def verify(self, request: VerificationRequest) -> Verdict:
        try:
            if request.kind is VerificationKind.DIMENSION:
                return self._dimension(request)
            if request.kind is VerificationKind.QUANTITY:
                return self._quantity(request)
        except _Incoherent as exc:
            return self._false(str(exc))
        except Exception as exc:  # never crash the pipeline
            return self._unknown(f"The units verifier could not process this: {exc}")
        return self._unknown("Unsupported request kind.")

    # ------------------------------------------------------------------ kinds
    def _dimension(self, request: VerificationRequest) -> Verdict:
        lhs, rhs = _parse(request.lhs), _parse(request.rhs)

        if not _units_in(lhs) and not _units_in(rhs):
            return self._unknown(
                "Neither side carries a unit, so there are no dimensions to "
                "compare. This is an arithmetic claim, not a dimensional one."
            )

        left = _coherent(lhs, request.lhs)
        right = _coherent(rhs, request.rhs)

        if left == right:
            return self._true(
                f"Both sides have dimensions {_describe(left)}, so they are "
                "dimensionally the same."
            )
        return self._false(
            f"Different dimensions: {request.lhs} is {_describe(left)} but "
            f"{request.rhs} is {_describe(right)}."
        )

    def _quantity(self, request: VerificationRequest) -> Verdict:
        lhs, rhs = _parse(request.lhs), _parse(request.rhs)

        if not _units_in(lhs) and not _units_in(rhs):
            return self._unknown(
                "Neither side carries a unit. A claim with no units is "
                "arithmetic; use the numeric check so the right verifier "
                "decides it."
            )

        left = _coherent(lhs, request.lhs)
        right = _coherent(rhs, request.rhs)

        # Dimensions first. A metre is never a second, and saying so is more
        # useful than a number comparison that was doomed before it started.
        if left != right:
            return self._false(
                f"Dimensionally impossible: {request.lhs} is "
                f"{_describe(left)} but {request.rhs} is {_describe(right)}."
            )

        target = _target_units(rhs)
        converted = convert_to(lhs, target) if target else lhs

        places = _places(request.tolerance)
        if places is not None:
            left_value, right_value = _magnitude(converted), _magnitude(rhs)
            if left_value is None or right_value is None:
                return self._unknown("Could not reduce a side to a number.")
            if round(left_value, places) == round(right_value, places):
                return self._true(
                    f"{request.lhs} = {left_value} {_unit_text(target)}, which "
                    f"rounds to {round(left_value, places)} at {places} decimal "
                    f"places, matching {request.rhs}."
                )
            return self._false(
                f"{request.lhs} = {left_value} {_unit_text(target)}, which "
                f"rounds to {round(left_value, places)} at {places} decimal "
                f"places, not {round(right_value, places)}."
            )

        difference = _magnitude(converted - rhs)
        if difference is None:
            return self._unknown(
                "Could not reduce the difference between the two sides to a number."
            )

        if abs(difference) <= _closeness(rhs):
            return self._true(
                f"{request.lhs} equals {request.rhs} "
                f"({_magnitude(converted)} {_unit_text(target)})."
            )
        return self._false(
            f"{request.lhs} = {_magnitude(converted)} {_unit_text(target)}, "
            f"not {_magnitude(rhs)}."
        )

    # ------------------------------------------------------------- helpers
    def _true(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.TRUE, self.name, detail)

    def _false(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.FALSE, self.name, detail)

    def _unknown(self, detail: str) -> Verdict:
        return Verdict(VerificationStatus.UNKNOWN, self.name, detail)
