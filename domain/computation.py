"""A value the system worked out, as opposed to a claim it checked.

WHY THIS IS NOT A VERDICT, and the distinction is the whole point.

A `Verdict` answers "is this claim true?". Everything about it — the guard's
aggregation, the banner, the faithfulness lint — exists because a language
model must not be able to assert its way to TRUE. The user's claimed value
is what cross-checks the model's formalisation: get the formula wrong and it
disagrees with the claim, and the disagreement surfaces as FALSE.

A `Computation` answers "what is this?". There is no claim, so that
cross-check is gone. What remains is:

    the VERIFIER produced the number, not the model      (as before)
    the model chose the FORMALISATION                    (as before)
    nothing independent checks that choice               (NEW, and the risk)

So a computation is reported as COMPUTED and never as VERIFIED. Those are
different words for a reason, and conflating them would give a wrong formula
the authority the guard was built to withhold from wrong prose.

WHAT SUBSTITUTES FOR THE MISSING CLAIM is the reader. The formula and its
inputs are shown, every time, in the words the user gave them — so a person
who asked about a pendulum can see that it computed 4*pi**2*L/T**2 and not
something else. That is weaker than a compiled check, and it is the honest
best available when nobody stated an answer to check against.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Computation:
    """A number the system derived, with everything needed to audit it."""

    request: str          # what was asked, in the user's words
    formula: str          # the formalisation the model chose
    inputs: str           # the values it fed in
    value: str            # the result, with units and error bar if any
    method: str           # which verifier produced it
    working: str = ""     # the terms, so a reader can follow it
    caveats: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return bool(self.value)

    def summary(self) -> str:
        return f"{self.formula} = {self.value}"

    def report(self) -> str:
        """The computation as a reader should see it.

        The formalisation comes FIRST and the number second, deliberately.
        A reader who skims will see the number either way; one who is
        checking needs to see what was computed before being told the
        answer, because the formula is the part nothing else verified.
        """
        lines = [
            f"  from  {self.formula}",
            f"  with  {self.inputs}",
            f"  gives {self.value}",
        ]
        if self.working:
            lines.append(f"        {self.working}")
        for caveat in self.caveats:
            lines.append(f"  note: {caveat}")
        return "\n".join(lines)
