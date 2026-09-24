"""What values a physical quantity is allowed to take.

Every bound here is a CONSEQUENCE OF PHYSICS, not a convention or a typical
range. That restriction is the whole design. A bound that merely describes
what is usual would refute unusual but correct answers, and a refutation
carries the full authority of the guard — it turns a verdict to FALSE on its
own. So a quantity belongs in this table only when a value outside the bound
is impossible, and the reason is recorded beside it.

Several tempting entries are deliberately ABSENT, listed at the bottom with
why. Leaving a check out costs a missed refutation; putting a wrong one in
costs a confident wrong answer, and those are not the same price.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    quantity: str
    low: float | None          # None means unbounded below
    high: float | None         # None means unbounded above
    unit: str
    because: str
    low_inclusive: bool = True
    high_inclusive: bool = True
    aliases: tuple[str, ...] = ()


_TABLE: tuple[Domain, ...] = (
    Domain(
        "probability", 0.0, 1.0, "",
        "a probability is a measure of the whole space normalised to 1",
        aliases=("chance", "likelihood"),
    ),
    Domain(
        "mole fraction", 0.0, 1.0, "",
        "a fraction of a total cannot exceed the total",
        aliases=("mass fraction", "fraction", "proportion"),
    ),
    Domain(
        "percentage yield", 0.0, 100.0, "%",
        "a reaction cannot produce more product than its stoichiometry allows",
        aliases=("percent yield", "yield"),
    ),
    Domain(
        "efficiency", 0.0, 1.0, "",
        "work out cannot exceed energy in without creating energy",
        aliases=("thermal efficiency",),
    ),
    Domain(
        "concentration", 0.0, None, "mol/L",
        "an amount of substance in a volume cannot be negative",
        aliases=("molarity", "molar concentration"),
    ),
    Domain(
        "mass", 0.0, None, "kg",
        "mass is non-negative",
        aliases=("weight in kilograms",),
    ),
    Domain(
        "volume", 0.0, None, "L",
        "a region of space cannot have negative extent",
    ),
    Domain(
        "amount of substance", 0.0, None, "mol",
        "a count of particles cannot be negative",
        aliases=("moles", "number of moles"),
    ),
    Domain(
        "absolute temperature", 0.0, None, "K",
        "the kelvin scale starts at absolute zero",
        aliases=("temperature in kelvin", "kelvin temperature"),
    ),
    Domain(
        "celsius temperature", -273.15, None, "degC",
        "nothing is colder than absolute zero, which is -273.15 degrees Celsius",
        aliases=("temperature in celsius",),
    ),
    Domain(
        "speed", 0.0, 299792458.0, "m/s",
        "no massive object reaches the speed of light",
        high_inclusive=False,
        aliases=("velocity magnitude", "speed of an object"),
    ),
    Domain(
        "wavelength", 0.0, None, "m",
        "a wavelength is a distance",
        low_inclusive=False,
    ),
    Domain(
        "frequency", 0.0, None, "Hz",
        "a rate of repetition cannot be negative",
    ),
    Domain(
        "half-life", 0.0, None, "s",
        "a half-life is a duration",
        low_inclusive=False,
        aliases=("halflife", "half life"),
    ),
    Domain(
        "rate constant", 0.0, None, "",
        "a rate constant is non-negative",
    ),
    Domain(
        "resistance", 0.0, None, "ohm",
        "an ordinary resistance is non-negative",
    ),
    Domain(
        "absolute pressure", 0.0, None, "Pa",
        "an absolute pressure is measured from vacuum",
        aliases=("pressure",),
    ),
    Domain(
        "count", 0.0, None, "",
        "a count of discrete things cannot be negative",
        aliases=("number of particles", "population", "cell count"),
    ),
    Domain(
        # The bounds are wide on purpose. pH is -log10 of the hydrogen ion
        # activity and is NOT confined to 0-14: concentrated hydrochloric
        # acid reaches about -1.1 and saturated sodium hydroxide about 15.0.
        # Bounding it at 0 and 14, as school textbooks do, would refute
        # correct answers. These bounds refute only the arithmetic slip that
        # produces a pH of 150.
        "pH", -2.0, 16.0, "",
        "pH outside roughly -2 to 16 is beyond any attainable aqueous solution",
    ),
)


# DELIBERATELY ABSENT, and why. Each of these was considered and rejected
# because no bound on it is a consequence of physics:
#
#   energy       can be negative; a bound electron has negative energy.
#   enthalpy     exothermic changes are negative by convention.
#   Gibbs energy negative is precisely the interesting case.
#   charge       negative charge is ordinary.
#   entropy      absolute entropy is non-negative, but the entropy CHANGE
#                asked about in problems is routinely negative, and the two
#                are not distinguishable from a bare number.
#   pressure     gauge pressure is negative below atmospheric; only ABSOLUTE
#                pressure is bounded, so only that name is in the table.
#   acceleration negative means "the other way".


def _key(text: str) -> str:
    return " ".join((text or "").strip().lower().replace("_", " ").split())


_BY_NAME: dict[str, Domain] = {}
for _domain in _TABLE:
    _BY_NAME[_key(_domain.quantity)] = _domain
    for _alias in _domain.aliases:
        _BY_NAME.setdefault(_key(_alias), _domain)


def find(quantity: str) -> Domain | None:
    return _BY_NAME.get(_key(quantity))


def known_quantities() -> list[str]:
    return sorted({domain.quantity for domain in _TABLE})


def violation(domain: Domain, value: float) -> str:
    """Why `value` is impossible for this quantity, or "" if it is not."""
    if domain.low is not None:
        if value < domain.low or (value == domain.low and not domain.low_inclusive):
            edge = "at most" if domain.low_inclusive else "below"
            _ = edge
            return (
                f"{value} is below the least possible {domain.quantity} "
                f"({domain.low}{' exclusive' if not domain.low_inclusive else ''}"
                f"{' ' + domain.unit if domain.unit else ''}): {domain.because}."
            )
    if domain.high is not None:
        if value > domain.high or (value == domain.high and not domain.high_inclusive):
            return (
                f"{value} exceeds the greatest possible {domain.quantity} "
                f"({domain.high}{' exclusive' if not domain.high_inclusive else ''}"
                f"{' ' + domain.unit if domain.unit else ''}): {domain.because}."
            )
    return ""
