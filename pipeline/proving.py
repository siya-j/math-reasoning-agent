"""Which prover runs, and how its cost is counted.

    MRA_PROVER=pipeline   the baseline: a fixed sequence of stateless calls
    MRA_PROVER=agentic    one conversation with tools and persistent state
    MRA_PROVER=math_v2    the blueprint Deep Agent package

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
MATH_V2 = "math_v2"


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


def environment() -> dict:
    """How the configured prover is executing, for the results file.

    THE SAME SEAM AS `prove`, and for the same reason. `scripts/
    evaluate_proofs.py` must not import `math_v2` — it drives whichever prover
    is configured and knowing about one of them by name is how a "switch"
    quietly becomes a dependency. So the evaluator asks here, and here is the
    one place that knows which prover is selected.

    Why it exists at all: the subprocess and REPL Lean backends differ by ~8x
    in wall clock, and a results file that cannot be attributed to one of them
    is not a measurement.
    """
    if config.PROVER == MATH_V2:
        try:
            from math_v2.tools import _repl

            return _repl.describe()
        except Exception:  # noqa: BLE001 - reporting must not break a run
            return {"prover": MATH_V2}
    return {"prover": config.PROVER}


# PutnamBench, and `hard`/`deep` -- a deliberately-larger step, NOT an
# attempt at SOTA's scale (LongCat-Flash-Prover: up to 220 attempts/problem;
# Seed-Prover 1.5: solve-time tail to 53h/problem; CAM-Bench's own protocol:
# 8h/instance; Aleph Prover: $23-68/problem on PutnamBench -- all financially
# and practically inappropriate for a cost-conscious individual on Gemini
# 3.5 Flash).
#
# MEASURED, not guessed, THE VALUES THEMSELVES: `hard-irrational-sqrt-sum`
# needed more than 12 real compiles to get from "every needed lemma proved"
# to "assembled" -- it overran the old default even past
# docs/hard-tier-candidates.md's own 5-8 estimate. MAX_LEAN_CALLS=40 clears
# that with real margin. MAX_SECONDS=3600 covers 40 compiles at ~45s
# worst-case each (1800s) plus headroom for search and model latency.
# MAX_TOOL_CALLS/MAX_SEARCHES scale with it at roughly the old defaults'
# ratios. MAX_CONSECUTIVE_SEARCHES is unchanged -- it bounds a RUN of
# searching with no compile in between, independent of the total budget's
# size. MAX_STATEMENT_CHECKS is also unchanged and deliberately absent: it
# bounds re-wording a SIGNATURE, and a harder proof does not make the
# signature harder to state.
#
# LIVES HERE, NOT IN `math_v2/core/budget.py`, EVEN THOUGH ITS CONTENT IS
# MATH_V2-SPECIFIC. MEASURED FAILURE: it used to live there, and
# `budget_profile` did `from math_v2.core import budget` to read it —
# which executes `budget.py`'s OWN module-level `os.getenv` reads
# immediately, before this function ever reaches the `os.environ.setdefault`
# loop below. The env vars ended up set correctly; `budget.py`'s constants,
# already computed from the OLD values by the time anything read the new
# ones, did not — a real PutnamBench run showed the profile's own banner
# printed while every goal still ran under the 12-compile default. Keeping
# the values here means applying the profile never has to import the module
# whose already-computed constants it exists to influence.
HARD_REASONING = {
    "MRA_MAX_AGENT_SECONDS": "3600",
    "MRA_MAX_AGENT_LEAN": "40",
    "MRA_MAX_AGENT_STEPS": "120",
    "MRA_MAX_AGENT_SEARCHES": "40",
    "MRA_MAX_CONSECUTIVE_SEARCHES": "3",
}


def budget_profile(name: str) -> dict:
    """Environment defaults for a named budget profile, applied via
    `os.environ.setdefault` — an env var already exported still wins.

    THE SAME SEAM AS `environment`/`prove`, for the same reason:
    `scripts/evaluate_proofs.py` must not import `math_v2` directly. Only
    the math_v2 prover has a budget-profile system today; on any other
    configured prover this applies nothing and returns {}.

    Deliberately does NOT import `math_v2.core.budget` to build `profile`
    below — see `HARD_REASONING`'s own comment for why that import order is
    exactly the bug this function must not reintroduce.
    """
    if config.PROVER != MATH_V2:
        return {}

    import os

    profile = {"hard-reasoning": HARD_REASONING}[name]
    for key, value in profile.items():
        os.environ.setdefault(key, value)
    return profile


def prove(goal: str, **kwargs) -> ProofRun:
    """Run the configured prover, instrumented identically either way."""
    from pipeline import agentic_prover, prover

    telemetry = Telemetry()
    started = time.monotonic()

    if config.PROVER == MATH_V2:
        # The blueprint agent. It keeps its own record on disk and re-derives
        # the verdict from it, so nothing is wrapped or counted here — the
        # instrumentation below exists to make the BASELINE comparable, and
        # imposing it would mean two sources of truth for one run.
        from math_v2 import harness

        return harness.prove(goal, **kwargs)

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
