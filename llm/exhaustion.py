"""Is a provider failure one that waiting cannot fix?

ONE DEFINITION, USED BY EVERYTHING THAT CALLS A MODEL.

This began as a private pair in `math_v2/harness.py`, where it stops the
transient-retry loop from backing off against a billing wall -- MEASURED:
132 seconds were spent retrying a monthly spending cap. Every other script
that calls a model needs the same distinction, and this project has already
paid three times for the same fact living in two hand-maintained places:

  * `scripts/evaluate_proofs.py`'s display map covered three of six
    outcomes and killed a live run on its first goal
  * `completed()` rebuilt ProofResult from ten named fields of twenty-four
    and destroyed a results file's telemetry
  * `budget._FIELDS` was three duplicate literals of one shape

So the phrases live here, once.

A HOPELESS failure is exhaustion of something only the account holder can
change: a spend cap, a quota, a billing state. Retrying is guaranteed to
fail and every attempt costs wall clock. A TRANSIENT failure is a transport
or server fault, where retrying is exactly right. The two must never be
confused in either direction -- backing off against a cap wastes time, and
giving up on a dropped connection wastes a run.
"""

from __future__ import annotations

# Substrings, lowercased, matched against the whole exception chain. Kept as
# phrases rather than status codes because 429 means BOTH "slow down"
# (transient, retry) and "you are out of money" (hopeless, stop) -- only the
# message separates them. The Gemini form is
#     429 RESOURCE_EXHAUSTED ... 'Your project has exceeded its monthly
#     spending cap.'
# which "spending cap" catches.
HOPELESS = (
    "spending cap",
    "billing",
    "quota exceeded",
    "exceeded your quota",
    "insufficient_quota",
    "payment required",
    "exceeded its monthly",
)


def is_hopeless(exc) -> bool:
    """Is this exhaustion that a backoff cannot resolve?

    Walks `__cause__`/`__context__` because provider SDKs wrap: the reason
    arrives inside whatever the client raises, and matching only the
    outermost message would miss it. Cycle-guarded, since a wrapped
    exception chain can loop.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        text = str(exc).lower()
        if any(phrase in text for phrase in HOPELESS):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def advice(exc) -> str:
    """What the operator should do about it, in plain words.

    A raw 429 with a JSON blob in it is not an instruction. This says which
    wall was hit and where it is changed, because nothing in the codebase
    can change it.
    """
    text = str(exc).lower()
    if "spending cap" in text or "exceeded its monthly" in text:
        return (
            "The project's monthly SPENDING CAP is exhausted. No model call "
            "will succeed until the cap is raised or the month rolls over -- "
            "this is an account setting, not a bug, and nothing here can "
            "work around it.\n"
            "  Google AI Studio: https://ai.studio/spend"
        )
    if "quota" in text or "insufficient_quota" in text:
        return ("A per-model or per-project QUOTA is exhausted. Waiting for "
                "the window to reset, or requesting more, is the only fix.")
    if "billing" in text or "payment required" in text:
        return ("BILLING is not active for this project. The provider will "
                "refuse every call until it is.")
    return "Exhaustion that retrying cannot resolve."
