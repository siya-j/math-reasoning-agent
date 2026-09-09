"""One definition of "waiting will not fix this", used by every model caller.

WHY IT IS SHARED. It began private in `math_v2/harness.py`, stopping the
transient-retry loop from backing off against a billing wall -- MEASURED:
132 seconds spent retrying a monthly spending cap. `scripts/contamination.py`
then needed the same distinction, and this project has paid three times for
one fact living in two hand-maintained places: the display map that covered
three of six outcomes and killed a live run, the `completed()` rebuild that
destroyed a results file's telemetry, and `budget._FIELDS` as three
duplicate literals.

THE DISTINCTION MUST HOLD BOTH WAYS. 429 means both "slow down" (transient,
retry) and "you are out of money" (hopeless, stop), so only the message
separates them. Backing off against a cap wastes time; giving up on a
dropped connection wastes a run.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.exhaustion import HOPELESS, advice, is_hopeless

# Verbatim from the run that hit it.
REAL_CAP = (
    "Error calling model 'gemini-3.5-flash' (RESOURCE_EXHAUSTED): 429 "
    "RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'Your project "
    "has exceeded its monthly spending cap. Please go to AI Studio at "
    "https://ai.studio/spend to manage your project spend cap. Learn more "
    "at https://ai.google.dev/gemini-api/docs/billing#project-spend-caps. ', "
    "'status': 'RESOURCE_EXHAUSTED'}}"
)


def test_the_real_spending_cap_error_is_hopeless():
    """The exact string the provider sent, not a paraphrase of it."""
    assert is_hopeless(RuntimeError(REAL_CAP))


# HARD-CODED, NOT DERIVED FROM `HOPELESS`.
#
# The first version of this test was `@parametrize("phrase", HOPELESS)`,
# which is vacuous: deleting a phrase from the list also deletes its test
# case, so the mutation that removed `spending cap` passed with one fewer
# test rather than failing. A guard whose scope is the thing it guards
# cannot catch a deletion.
MUST_BE_HOPELESS = (
    "spending cap",
    "billing",
    "quota exceeded",
    "exceeded your quota",
    "insufficient_quota",
    "payment required",
)


@pytest.mark.parametrize("phrase", MUST_BE_HOPELESS)
def test_every_required_phrase_is_still_recognised(phrase):
    """Each one pinned separately, so removing any single phrase fails --
    even when another happens to match the same real-world message."""
    assert phrase in HOPELESS, f"{phrase!r} was removed from HOPELESS"
    assert is_hopeless(RuntimeError(f"429: {phrase.upper()} happened"))


@pytest.mark.parametrize("message", [
    "ReadError: connection reset by peer",
    "503 Service Unavailable",
    "429 RESOURCE_EXHAUSTED: too many requests, please retry",
    "timed out waiting for response",
    "Internal error encountered",
])
def test_a_recoverable_fault_is_not_hopeless(message):
    """The other direction, and the more expensive one to get wrong: giving
    up on a transport fault throws away a run. Note the bare 429 -- a rate
    limit carries the same code as a spend cap and must still be retried."""
    assert not is_hopeless(RuntimeError(message))


def test_a_wrapped_cause_is_found():
    """Provider SDKs wrap, so the reason arrives inside whatever the client
    raises and matching only the outermost message would miss it."""
    inner = RuntimeError(REAL_CAP)
    outer = RuntimeError("Error calling model")
    outer.__cause__ = inner
    assert is_hopeless(outer)


def test_a_cyclic_exception_chain_does_not_hang():
    """A wrapped chain can loop; the walk is cycle-guarded."""
    first = RuntimeError("one")
    second = RuntimeError("two")
    first.__cause__ = second
    second.__cause__ = first
    assert is_hopeless(first) is False


def test_the_advice_names_the_wall_and_where_to_change_it():
    """A raw 429 with a JSON blob in it is not an instruction. Nothing in
    this codebase can raise a spend cap, so the message has to say so."""
    text = advice(RuntimeError(REAL_CAP))
    assert "SPENDING CAP" in text
    assert "ai.studio/spend" in text
    assert "not a bug" in text


def test_the_advice_distinguishes_quota_from_a_cap():
    """Different walls, different remedies -- a quota resets on its own, a
    cap does not."""
    quota = advice(RuntimeError("429: you have exceeded your quota for this model"))
    assert "QUOTA" in quota
    assert "reset" in quota
    billing = advice(RuntimeError("402 payment required: billing is not enabled"))
    assert "BILLING" in billing


def test_the_harness_uses_this_definition_and_not_a_copy():
    """The whole point of the move. If the harness ever grows its own list
    again, the two will drift and one caller will retry a billing wall."""
    import math_v2.harness as harness

    assert harness._is_hopeless is is_hopeless
    source = Path(harness.__file__).read_text(encoding="utf-8")
    assert '"spending cap"' not in source, "the harness has its own list again"
