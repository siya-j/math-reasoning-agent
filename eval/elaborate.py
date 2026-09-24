"""What a Lean statement ACTUALLY says, as opposed to what it looks like.

WHY THIS EXISTS. The round-trip check in eval/roundtrip.py back-translated
the SOURCE TEXT of a formal statement. On its first real run it called four
genuinely broken ProofNet statements faithful, because in Lean the source and
the elaborated term routinely differ:

    Icc 0 1        reads as the unit interval;
                   elaborated to Icc (0 : ℕ) (1 : ℕ), a two-point set,
                   which makes "x^n does not converge uniformly" FALSE

    finrank V ≥ 2  reads as "dimension at least 2";
                   elaborated with V AS THE RING, leaving finrank V a
                   partially applied function and coercing 2 into a
                   matching function type

A model shown the source describes what a mathematician would ASSUME it
means. That is exactly the wrong reading for an audit, because the bug is
always in the gap between assumption and elaboration.

HERALD-AF (REAL-Prover, arXiv:2505.20613) gets away with round-tripping
source because it audits statements it has just GENERATED, in a context it
controls. Auditing someone else's formalisations is a harder problem, and
the method does not port unchanged.

TWO TIERS OF EVIDENCE, and they are not equal:

    the compiler   says whether a statement elaborates at all, and to what.
                   BOUGHT, in the sense the guard means. Decisive.
    the model      reads the elaborated form and compares it to a textbook.
                   Advisory, and only worth asking once the first tier
                   passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from verifiers.lean_runner import LeanOutcome, run_lean

# Printing options that reveal the choices a reader would otherwise supply
# for themselves. `pp.numericTypes` is the one that matters most: it is what
# turns a plausible `Icc 0 1` into the damning `Icc (0 : ℕ) (1 : ℕ)`.
OPTIONS = (
    "set_option pp.numericTypes true\n"
    "set_option pp.coercions true\n"
)

_NAME = re.compile(r"^\s*(?:theorem|lemma)\s+([^\s({\[:]+)")
_DIAGNOSTIC = re.compile(r"^\S+\.lean:\d+:\d+: (error|warning|info):")


@dataclass(frozen=True)
class Elaboration:
    """What Lean made of a statement."""

    ok: bool
    text: str = ""       # the elaborated type, as Lean printed it
    problem: str = ""    # why it is not usable, when ok is False

    @property
    def usable(self) -> bool:
        return self.ok and bool(self.text.strip())


def name_of(statement: str) -> str:
    found = _NAME.match(statement or "")
    return found.group(1) if found else ""


def source_for(preamble: str, statement: str) -> str:
    """A file that declares the statement and asks Lean to print its type."""
    name = name_of(statement)
    return "\n".join([
        (preamble or "import Mathlib").rstrip(),
        "",
        OPTIONS,
        statement.rstrip() + " := sorry",
        "",
        f"#check @{name}" if name else "",
    ])


def _printed_type(output: str, name: str) -> str:
    """The `#check` output: everything from the line naming the theorem
    until the next compiler diagnostic."""
    lines = output.splitlines()
    collected: list[str] = []
    for line in lines:
        if collected:
            if _DIAGNOSTIC.match(line):
                break
            collected.append(line.rstrip())
            continue
        stripped = line.strip()
        if stripped.startswith(f"{name} :") or stripped.startswith(f"@{name} :"):
            collected.append(line.rstrip())
    return "\n".join(collected).strip()


def elaborate(preamble: str, statement: str, run=run_lean,
              timeout: int = 300) -> Elaboration:
    """Ask Lean what this statement means. Never raises."""
    name = name_of(statement)
    if not name:
        return Elaboration(False, problem="could not find the theorem's name")

    try:
        result = run(source_for(preamble, statement), timeout=timeout)
    except Exception as exc:
        return Elaboration(False, problem=f"Lean could not be run: {exc}")

    if result.outcome is LeanOutcome.UNAVAILABLE:
        return Elaboration(False, problem="no Lean on this machine")
    if result.outcome is LeanOutcome.TIMEOUT:
        return Elaboration(False, problem="Lean timed out")

    printed = _printed_type(result.output, name)

    if result.outcome is LeanOutcome.ERRORS:
        return Elaboration(
            False,
            text=printed,
            problem=("the statement does not elaborate: "
                     + (result.first_error or "compiler errors")),
        )

    # `sorry` INSIDE THE PRINTED TYPE is different from the expected
    # "declaration uses `sorry`" warning, which comes from the proof we
    # deliberately left out. In the type it means Lean could not make sense
    # of part of the STATEMENT and replaced it with a hole -- which is how
    # Munkres_exercise_25_9 came to end in `→ sorry` after autoImplicit
    # silently bound an unknown identifier as a free variable.
    if "sorry" in printed:
        return Elaboration(
            False,
            text=printed,
            problem=("the elaborated statement contains `sorry`, so part of "
                     "it could not be understood by Lean"),
        )

    if not printed:
        return Elaboration(
            False, problem="Lean printed no type for this statement"
        )

    return Elaboration(True, text=printed)
