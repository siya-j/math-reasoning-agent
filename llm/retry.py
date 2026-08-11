"""Retrying model calls that failed for reasons that may not recur.

The verification path has had backoff since Phase 4 (`eval/runner.py`). The
proving path had none, so a transient limit ended a run that the other path
would have survived. This module gives both the same behaviour.

WHAT IS RETRIED, AND WHY THAT LIST
----------------------------------
Retrying a malformed request wastes quota and cannot succeed, so the default
is NOT to retry. Two categories are exceptions:

  TRANSIENT   explicitly temporary — 429, RESOURCE_EXHAUSTED, 503, timeouts.
              Waiting is exactly the right response.

  AMBIGUOUS   INVALID_ARGUMENT. Normally permanent, but Gemini was observed
              returning it for every call — including two-line prompts with
              no premises — immediately after a long run. That behaves like
              exhaustion rather than a bad request, so it gets ONE retry.
              If it really is malformed, one wasted call is a cheap price for
              not losing a run to a mislabelled limit.
"""

from __future__ import annotations

import time

TRANSIENT = (
    "429",
    "resource_exhausted",
    "rate limit",
    "quota",
    "503",
    "unavailable",
    "deadline",
    "timeout",
    "temporarily",
)

AMBIGUOUS = ("invalid_argument",)


def _classify(error: Exception) -> str:
    text = str(error).lower()
    if any(marker in text for marker in TRANSIENT):
        return "transient"
    if any(marker in text for marker in AMBIGUOUS):
        return "ambiguous"
    return "permanent"


def call_with_backoff(call, attempts: int = 3, backoff: float = 20.0, sleep=time.sleep):
    """Run `call()`, retrying failures that waiting might fix.

    `sleep` is injected so tests do not actually wait.
    """
    last: Exception | None = None

    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            last = exc
            kind = _classify(exc)

            if kind == "permanent":
                raise
            # An ambiguous error gets one retry, not the full ladder.
            if kind == "ambiguous" and attempt >= 1:
                raise
            if attempt == attempts - 1:
                raise

            sleep(backoff * (attempt + 1))

    raise last  # unreachable, but keeps the contract explicit
