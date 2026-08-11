"""Which prover runs, and how its cost is counted.

    MRA_PROVER=pipeline   the baseline: a fixed sequence of stateless calls
    MRA_PROVER=agentic    one conversation with tools and persistent state

A SWITCH, NOT A REPLACEMENT
---------------------------
`pipeline/prover.py` is unchanged and remains the default. Both are runnable
on the same goals so the question "does conversation beat a pipeline?" is
answerable with numbers — the discipline the v2 experiment taught, where
model-directed control cost coverage and nobody could say how much until it
was measured.

INSTRUMENTATION BY INJECTION
----------------------------
The baseline already accepts `formalizer` and `check` as arguments, so its
cost is counted by wrapping what is passed IN. Nothing inside it changes.
"""

from __future__ import annotations

import time

import config
from domain.proof import ProofRun, Telemetry
from llm.formalizer import Formalizer

PIPELINE = "pipeline"
AGENTIC = "agentic"


class CountingFormalizer:
    """Wraps a Formalizer and counts the model calls it makes.

    Delegates everything, including capabilities added later — an
    optional method the baseline probes for with `getattr` must still be
    visible through the wrapper.
    """

    def __init__(self, inner, telemetry: Telemetry):
        self._inner = inner
        self._telemetry = telemetry

    def __getattr__(self, name: str):
        attribute = getattr(self._inner, name)
        if not callable(attribute) or name.startswith("_"):
            return attribute

        # `premises_for` is retrieval, not generation. Counting it as a model
        # call would make the two provers incomparable.
        counter = "retrieval_calls" if name == "premises_for" else "model_calls"

        def counted(*args, **kwargs):
            setattr(
                self._telemetry, counter, getattr(self._telemetry, counter) + 1
            )
            return attribute(*args, **kwargs)

        return counted


def counting_check(check, telemetry: Telemetry):
    """Wrap a compile function so every invocation is counted."""

    def counted(statement: str, proof: str):
        telemetry.lean_calls += 1
        return check(statement, proof)

    return counted


def prove(goal: str, **kwargs) -> ProofRun:
    """Run the configured prover, instrumented identically either way."""
    from pipeline import agentic_prover, prover

    telemetry = Telemetry()
    started = time.monotonic()

    if config.PROVER == AGENTIC:
        run = agentic_prover.prove(goal, **kwargs)
        # The agentic prover writes into its own run's telemetry.
        run.telemetry.seconds = run.telemetry.seconds or (
            time.monotonic() - started
        )
        return run

    # Baseline: instrument by wrapping the injectables, touching nothing.
    #
    # NOT `setdefault` — its default argument is evaluated eagerly, so it
    # would build a real Formalizer (and a real model) even when the caller
    # supplied one. That made every offline test demand an API key.
    if kwargs.get("formalizer") is None:
        kwargs["formalizer"] = Formalizer()
    kwargs["formalizer"] = CountingFormalizer(kwargs["formalizer"], telemetry)

    kwargs["check"] = counting_check(
        kwargs.get("check") or prover.lean_check, telemetry
    )
    kwargs["structure_check"] = _counting_structure(
        kwargs.get("structure_check") or prover.lean_structure_ok, telemetry
    )

    run = prover.prove(goal, **kwargs)
    telemetry.seconds = time.monotonic() - started
    run.telemetry = telemetry
    return run


def _counting_structure(structure_check, telemetry: Telemetry):
    def counted(statement: str, proof: str) -> bool:
        telemetry.lean_calls += 1
        return structure_check(statement, proof)

    return counted
