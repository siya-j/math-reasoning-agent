"""Standard atomic weights, and a parser for chemical formulae.

SOURCE. The weights are the IUPAC standard atomic weights (the 2021 table),
in unified atomic mass units. Elements whose standard weight IUPAC publishes
as an interval (hydrogen, carbon, oxygen, sulfur, chlorine and others) are
recorded here as the conventional single value, which is what a textbook
question expects and what the answer to such a question is graded against.

PRECISION, stated plainly. These are the values a chemistry course uses, not
the last digit of the CODATA evaluation. A molar mass computed from them is
correct to the precision of the table and no further, so the verifier reports
the arithmetic it performed rather than only its verdict, and rounds to the
precision the question asks for.
"""

from __future__ import annotations

import re

# name -> standard atomic weight (u)
ATOMIC_WEIGHTS: dict[str, float] = {
    "H": 1.008,
    "He": 4.002602,
    "Li": 6.94,
    "Be": 9.0121831,
    "B": 10.81,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998403162,
    "Ne": 20.1797,
    "Na": 22.98976928,
    "Mg": 24.305,
    "Al": 26.9815384,
    "Si": 28.085,
    "P": 30.973761998,
    "S": 32.06,
    "Cl": 35.45,
    "Ar": 39.95,
    "K": 39.0983,
    "Ca": 40.078,
    "Sc": 44.955908,
    "Ti": 47.867,
    "V": 50.9415,
    "Cr": 51.9961,
    "Mn": 54.938043,
    "Fe": 55.845,
    "Co": 58.933194,
    "Ni": 58.6934,
    "Cu": 63.546,
    "Zn": 65.38,
    "Ga": 69.723,
    "Ge": 72.630,
    "As": 74.921595,
    "Se": 78.971,
    "Br": 79.904,
    "Kr": 83.798,
    "Rb": 85.4678,
    "Sr": 87.62,
    "Y": 88.90584,
    "Zr": 91.224,
    "Nb": 92.90637,
    "Mo": 95.95,
    "Ru": 101.07,
    "Rh": 102.90549,
    "Pd": 106.42,
    "Ag": 107.8682,
    "Cd": 112.414,
    "In": 114.818,
    "Sn": 118.710,
    "Sb": 121.760,
    "Te": 127.60,
    "I": 126.90447,
    "Xe": 131.293,
    "Cs": 132.90545196,
    "Ba": 137.327,
    "La": 138.90547,
    "Ce": 140.116,
    "W": 183.84,
    "Pt": 195.084,
    "Au": 196.966570,
    "Hg": 200.592,
    "Tl": 204.38,
    "Pb": 207.2,
    "Bi": 208.98040,
    "Th": 232.0377,
    "U": 238.02891,
}


class FormulaError(ValueError):
    """A formula that cannot be read as a chemical formula."""


# An element symbol is a capital followed by optional lowercase letters. The
# lowercase part matters: Co is cobalt and CO is carbon monoxide, and a
# parser that ignores case silently answers a different question.
_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)|(\()|(\))(\d*)|(\s+)|(\*|\.|·)(\d*)")


def parse_formula(formula: str) -> dict[str, int]:
    """Count the atoms of each element in a chemical formula.

    Handles nested groups, Ca(OH)2, and hydrates written with a dot or a
    star, CuSO4.5H2O. Returns element -> count.

    Raises FormulaError rather than guessing. An unreadable formula must not
    become a molar mass, because a wrong molar mass looks exactly like a
    right one.
    """
    text = (formula or "").strip()
    if not text:
        raise FormulaError("empty formula")

    # A hydrate multiplier applies to everything after the dot, so the
    # simplest correct reading is to split there and recurse.
    for separator in (".", "*", "·"):
        if separator in text:
            head, _, tail = text.partition(separator)
            multiplier, rest = _leading_count(tail)
            counts = parse_formula(head)
            for element, n in parse_formula(rest).items():
                counts[element] = counts.get(element, 0) + n * multiplier
            return counts

    stack: list[dict[str, int]] = [{}]
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise FormulaError(
                f"could not read '{formula}' at position {position}: "
                f"'{text[position:position + 8]}'"
            )
        position = match.end()
        element, count, opening, closing, group_count, space, _, _ = match.groups()

        if space:
            continue
        if opening:
            stack.append({})
            continue
        if closing is not None and closing == ")":
            if len(stack) == 1:
                raise FormulaError(f"unbalanced brackets in '{formula}'")
            group = stack.pop()
            times = int(group_count) if group_count else 1
            for name, n in group.items():
                stack[-1][name] = stack[-1].get(name, 0) + n * times
            continue
        if element:
            if element not in ATOMIC_WEIGHTS:
                raise FormulaError(
                    f"'{element}' is not an element in the table "
                    f"(reading '{formula}')"
                )
            stack[-1][element] = stack[-1].get(element, 0) + (
                int(count) if count else 1
            )

    if len(stack) != 1:
        raise FormulaError(f"unbalanced brackets in '{formula}'")
    if not stack[0]:
        raise FormulaError(f"no elements found in '{formula}'")
    return stack[0]


def _leading_count(text: str) -> tuple[int, str]:
    """Split '5H2O' into (5, 'H2O')."""
    match = re.match(r"\s*(\d*)\s*(.*)$", text, re.S)
    digits, rest = match.group(1), match.group(2)
    return (int(digits) if digits else 1), rest


def molar_mass(formula: str) -> tuple[float, str]:
    """The molar mass of a formula in g/mol, with the arithmetic that gave it.

    The second value exists so a verdict can be audited by a human without
    trusting this function. A molar mass reported with no working is an
    assertion; reported with its terms it is evidence.
    """
    counts = parse_formula(formula)
    total = 0.0
    terms = []
    for element in sorted(counts):
        n = counts[element]
        weight = ATOMIC_WEIGHTS[element]
        total += n * weight
        terms.append(f"{n}x{element}({weight})" if n != 1 else f"{element}({weight})")
    return total, " + ".join(terms)
