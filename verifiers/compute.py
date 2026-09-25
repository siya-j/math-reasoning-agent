"""Working a value out, when nobody stated one to check.

A researcher rarely has a claim. They have a question: what is g from these
measurements, what is this in SI, what is the uncertainty on that ratio. The
rest of this package decides claims, which means such a user must first
guess an answer so the system can check it -- backwards, and the point at
which most people stop.

THE NUMBER STILL COMES FROM A VERIFIER, NEVER FROM THE MODEL. That part of
the invariant is untouched: the model chooses the formula and the inputs, a
deterministic engine evaluates them, and the result is reported as COMPUTED
rather than VERIFIED because no claim was checked. See domain/computation.py
for why those must stay different words.

ONE DISPATCHER, THREE ENGINES, chosen by what the inputs carry:

    an error bar   -> uncertainty propagation, and the answer has one too
    a unit         -> the units engine, and the answer carries its unit
    neither        -> plain symbolic arithmetic

Picking by the INPUTS rather than asking the model which engine to use is
deliberate. It is one less thing the model can get wrong, and the choice is
recoverable from the recorded request afterwards.

WHAT IT REFUSES. Anything it cannot evaluate to a definite value. A
computation that returns something shaped like a number but derived from a
formula the engine could not make sense of is worse than no answer, because
the reader has no claim of their own to notice it against.
"""

from __future__ import annotations

from domain.computation import Computation
from science import units as science_units
from science.uncertainty import (
    UncertaintyError,
    parse_measurements,
    propagate,
)


class ComputeError(ValueError):
    """The request could not be turned into a value."""


def _carries_uncertainty(measurements) -> bool:
    return any(m.uncertainty for m in measurements.values())


def _carries_units(measurements) -> bool:
    return any(m.unit for m in measurements.values())


def compute(request: str, formula: str, inputs: str) -> Computation:
    """Evaluate `formula` at `inputs`. Raises ComputeError if it cannot.

    `inputs` is the same `name = value` form the uncertainty tool takes, so
    a value may carry a unit, an error bar, both or neither, and the engine
    follows from that rather than from a second decision by the model.
    """
    if not (formula or "").strip():
        raise ComputeError("no formula to evaluate")
    if not (inputs or "").strip():
        raise ComputeError(
            "no inputs given. Every name in the formula needs a value, "
            "written as name = value with its unit if it has one."
        )

    try:
        measurements = parse_measurements(inputs)
        result = propagate(formula, measurements)
    except UncertaintyError as exc:
        raise ComputeError(str(exc)) from None
    except Exception as exc:
        raise ComputeError(f"could not evaluate '{formula}': {exc}") from None

    measurement = result.result
    if measurement.value is None:
        raise ComputeError(
            f"'{formula}' did not reduce to a definite value. A number shaped "
            "like an answer, from a formula the engine could not make sense "
            "of, is worse than no answer."
        )

    # The engine is named from what the inputs carried, so a reader can tell
    # which machinery produced the number without reading this file.
    if _carries_uncertainty(measurements):
        method = "uncertainty"
    elif _carries_units(measurements):
        method = "units"
    else:
        method = "sympy"

    return Computation(
        request=request,
        formula=formula,
        inputs=inputs,
        value=str(measurement),
        method=method,
        working=result.working if _carries_uncertainty(measurements) else "",
        caveats=result.caveats,
    )


def dimensions_of(computation: Computation) -> str:
    """The result's dimensions, for the reader to sanity-check against.

    Not a verdict -- nobody said what the dimensions SHOULD be. But a
    pendulum answer in seconds rather than metres per second squared is the
    kind of thing a person spots instantly once it is printed, and printing
    it is the cheapest substitute available for the claim that is missing.
    """
    unit = getattr(computation, "value", "")
    for token in unit.split():
        if science_units.mentions_units(token):
            try:
                parsed = science_units.parse(token)
            except Exception:
                return ""
            from sympy.physics.units.systems.si import SI
            try:
                return str(SI.get_dimensional_expr(parsed))
            except Exception:
                return ""
    return ""
