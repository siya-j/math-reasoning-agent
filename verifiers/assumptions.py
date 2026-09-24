"""What is assumed about the symbols in a claim.

WHY IT IS NEEDED. Nearly every identity a scientist wants checked carries
conditions. The Gaussian integral is sqrt(pi/a) only for a > 0; sqrt(x**2)
is x only for x >= 0; a series converges only for |r| < 1. Without a way to
state them SymPy correctly refuses to commit and returns a Piecewise, and
the check is useless:

    integrate(exp(-a*x**2), (x, -oo, oo))
      -> Piecewise((sqrt(pi)*sqrt(1/a), Abs(arg(a)) <= pi/2), (..., True))

SymPy already has the mechanism -- Symbol('a', positive=True). This module
is only a safe, readable way to say so from a tool argument.

THE HAZARD, AND IT IS THE WHOLE REASON THIS FILE HAS A DOCSTRING THIS LONG.

An assumption can turn a FALSE claim TRUE.

    is Abs(x) equal to x?                        FALSE
    is Abs(x) equal to x, assuming x > 0?        TRUE

Both are correct answers to different questions. So a model that may choose
its own assumptions can make any claim about a variable come out true, by
narrowing the question until it does -- the same silent-correction failure
the faithfulness lint was written for, in a form the lint cannot see,
because it changes no numbers.

Three defences, none of which is sufficient alone:

  the assumptions are STATED in every verdict, so a conditional truth can
  never be read as an unconditional one;
  the tool's docstring says they must come from the question;
  nothing is assumed by default -- a claim with no stated assumptions is
  checked over the widest domain SymPy will consider, which is the strict
  reading.
"""

from __future__ import annotations

import re

# SymPy's own assumption names, allow-listed. An unknown one is refused
# rather than ignored: silently dropping "a is a unicorn" would check a
# weaker claim than the caller asked for and say nothing about it.
KNOWN = (
    "real integer rational irrational complex positive negative "
    "nonnegative nonpositive nonzero even odd prime finite infinite"
).split()

# Plain-English forms a question is likely to use.
_SYNONYMS = {
    "natural": ("integer", "nonnegative"),
    "naturals": ("integer", "nonnegative"),
    "whole": ("integer",),
    "integers": ("integer",),
    "reals": ("real",),
    "strictly": (),          # "strictly positive" -> positive
    "non-zero": ("nonzero",),
    "non-negative": ("nonnegative",),
    "non-positive": ("nonpositive",),
}

_RELATION = re.compile(
    r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*(>=|<=|!=|>|<)\s*0\s*$"
)

_RELATION_MEANS = {
    ">": "positive",
    ">=": "nonnegative",
    "<": "negative",
    "<=": "nonpositive",
    "!=": "nonzero",
}

_NAME = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


class AssumptionError(ValueError):
    """Text that does not describe an assumption."""


def parse(text: str) -> dict[str, dict[str, bool]]:
    """Read `a > 0, n positive integer, x real` into SymPy assumptions."""
    found: dict[str, dict[str, bool]] = {}
    for clause in (text or "").split(","):
        clause = clause.strip()
        if not clause:
            continue

        relation = _RELATION.match(clause)
        if relation:
            name = relation.group(1)
            flags = {_RELATION_MEANS[relation.group(2)]: True}
        else:
            name, flags = _words(clause)

        if not _NAME.match(name):
            raise AssumptionError(f"'{name}' is not a symbol name")
        found.setdefault(name, {}).update(flags)
    return found


def _words(clause: str) -> tuple[str, dict[str, bool]]:
    """Read `n positive integer` or `x: real`."""
    body = clause.replace(":", " ")
    parts = [p for p in body.replace("is", " ").split() if p]
    if len(parts) < 2:
        raise AssumptionError(
            f"'{clause.strip()}' does not say anything about a symbol. Write "
            "it as `a > 0` or `n positive integer`."
        )

    name, words = parts[0], parts[1:]
    flags: dict[str, bool] = {}
    for word in words:
        lowered = word.lower()
        if lowered in _SYNONYMS:
            for expanded in _SYNONYMS[lowered]:
                flags[expanded] = True
            continue
        if lowered not in KNOWN:
            raise AssumptionError(
                f"'{word}' is not an assumption this understands. Known: "
                + ", ".join(KNOWN)
            )
        flags[lowered] = True
    if not flags:
        raise AssumptionError(f"'{clause.strip()}' names no assumption")
    return name, flags


def symbols(assumptions: dict[str, dict[str, bool]]) -> dict:
    """The SymPy symbols those assumptions describe."""
    import sympy

    built = {}
    for name, flags in assumptions.items():
        try:
            built[name] = sympy.Symbol(name, **flags)
        except Exception as exc:
            raise AssumptionError(
                f"SymPy rejected the assumptions on '{name}': {exc}"
            ) from None
    return built


def describe(assumptions: dict[str, dict[str, bool]]) -> str:
    """The assumptions in words, for a verdict to state.

    Every verdict reached under assumptions must carry them. A conditional
    truth read as an unconditional one is the failure this guards against,
    and a detail string is where a reader would catch it.
    """
    if not assumptions:
        return ""
    parts = []
    for name in sorted(assumptions):
        flags = " and ".join(sorted(assumptions[name]))
        parts.append(f"{name} is {flags}")
    return "; ".join(parts)


def build(text: str) -> tuple[dict, str]:
    """Parse and build in one step. Returns (symbols, description)."""
    parsed = parse(text)
    return symbols(parsed), describe(parsed)
