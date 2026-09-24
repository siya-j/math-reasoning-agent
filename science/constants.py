"""Physical constants, and whether each one is exact.

THE DISTINCTION THAT MATTERS. Since the 2019 revision of the SI, several
constants are EXACT BY DEFINITION: the metre, kilogram, second, ampere,
kelvin and mole are now defined so that c, h, e, k and N_A have fixed values
with no uncertainty at all. Others — the gravitational constant above all —
remain MEASURED, and are known only to a stated uncertainty.

A verifier must treat these differently. An exact constant can be decided to
any precision asked of it. A measured one cannot: a claim stated more
precisely than the measurement supports is not false, it is undecided, and
answering FALSE would be asserting knowledge nobody has. So each entry
records its uncertainty, and `None` means exact.

SOURCE. CODATA 2018, as adopted in the 2019 SI. Units are SI base units and
are recorded as text beside each value so that a verdict can name them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Constant:
    name: str
    value: float
    unit: str
    uncertainty: float | None  # None means exact by definition
    aliases: tuple[str, ...] = ()

    @property
    def is_exact(self) -> bool:
        return self.uncertainty is None


_TABLE: tuple[Constant, ...] = (
    # --- exact by definition (2019 SI) --------------------------------
    Constant("speed of light", 299792458.0, "m/s", None,
             ("c", "speed_of_light", "light speed")),
    Constant("Planck constant", 6.62607015e-34, "J s", None,
             ("h", "planck", "planck constant")),
    Constant("elementary charge", 1.602176634e-19, "C", None,
             ("e", "elementary_charge", "charge of an electron",
              "electron charge")),
    Constant("Boltzmann constant", 1.380649e-23, "J/K", None,
             ("k", "k_B", "kB", "boltzmann")),
    Constant("Avogadro constant", 6.02214076e23, "1/mol", None,
             ("N_A", "NA", "avogadro", "avogadro number",
              "avogadro's number", "avogadros number")),
    Constant("molar gas constant", 8.31446261815324, "J/(mol K)", None,
             ("R", "gas constant", "universal gas constant",
              "molar_gas_constant")),
    Constant("standard acceleration of gravity", 9.80665, "m/s^2", None,
             ("g", "g_0", "standard gravity", "acceleration due to gravity")),
    Constant("standard atmosphere", 101325.0, "Pa", None,
             ("atm", "atmosphere", "standard pressure")),
    Constant("Faraday constant", 96485.33212, "C/mol", None,
             ("F", "faraday")),
    Constant("molar volume of an ideal gas at STP", 22.41396954, "L/mol", None,
             ("molar volume", "Vm", "molar volume at STP")),
    Constant("absolute zero", -273.15, "degC", None,
             ("absolute zero in celsius",)),

    # --- measured, with uncertainty -----------------------------------
    Constant("gravitational constant", 6.67430e-11, "m^3/(kg s^2)", 1.5e-15,
             ("G", "big G", "newtonian constant of gravitation")),
    Constant("electron mass", 9.1093837015e-31, "kg", 2.8e-40,
             ("m_e", "me", "mass of an electron", "electron rest mass")),
    Constant("proton mass", 1.67262192369e-27, "kg", 5.1e-37,
             ("m_p", "mp", "mass of a proton")),
    Constant("neutron mass", 1.67492749804e-27, "kg", 9.5e-37,
             ("m_n", "mn", "mass of a neutron")),
    Constant("fine-structure constant", 7.2973525693e-3, "", 1.1e-12,
             ("alpha", "fine structure constant")),
    Constant("Rydberg constant", 10973731.568160, "1/m", 2.1e-5,
             ("R_inf", "rydberg")),
    Constant("atomic mass constant", 1.66053906660e-27, "kg", 5.0e-37,
             ("u", "amu", "unified atomic mass unit", "dalton")),
)


def _key(text: str) -> str:
    return " ".join((text or "").strip().lower().replace("_", " ").split())


# Case-sensitive symbols, checked BEFORE the lowercased names.
#
# This is not fussiness. G is the gravitational constant, 6.674e-11, and g is
# the acceleration due to gravity, 9.80665 - two different constants eleven
# orders of magnitude apart that differ only in case. Lowercasing the lookup
# collapses them, and the collapse is silent: the verifier would confidently
# decide a claim about one using the value of the other.
_SYMBOLS: dict[str, str] = {
    "c": "speed of light",
    "h": "Planck constant",
    "e": "elementary charge",
    "k": "Boltzmann constant",
    "k_B": "Boltzmann constant",
    "N_A": "Avogadro constant",
    "R": "molar gas constant",
    "g": "standard acceleration of gravity",
    "G": "gravitational constant",
    "F": "Faraday constant",
    "u": "atomic mass constant",
    "m_e": "electron mass",
    "m_p": "proton mass",
    "m_n": "neutron mass",
}

_BY_NAME: dict[str, Constant] = {}
for _constant in _TABLE:
    _BY_NAME[_key(_constant.name)] = _constant
    for _alias in _constant.aliases:
        _BY_NAME.setdefault(_key(_alias), _constant)


def find(name: str) -> Constant | None:
    """Look up a constant by name or symbol, or None.

    The symbol table is consulted first and case-sensitively, so G and g do
    not become each other.
    """
    exact = (name or "").strip()
    if exact in _SYMBOLS:
        return _BY_NAME[_key(_SYMBOLS[exact])]
    return _BY_NAME.get(_key(exact))


def known_names() -> list[str]:
    return sorted({constant.name for constant in _TABLE})
