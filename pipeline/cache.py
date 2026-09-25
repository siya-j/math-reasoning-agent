"""Same question, same answer.

WHY A PRODUCT NEEDS THIS AND A BENCHMARK DOES NOT. Run-to-run variance was
measured at roughly one goal in six on the proving path, and individual
goals were seen proving anywhere from 1 time in 3 to 4 times in 5 across
identical runs. For a benchmark that is a measurement problem, answered by
pass@k. For a user it is something worse: ask the same question twice and
get two answers, from a tool whose entire claim is that it does not guess.

A cache does not make the agent more capable. It makes it CONSISTENT, which
is a different and more visible property.

WHAT IT MUST NEVER DO, and these are the reasons this file is not a dict:

  It must not turn an unverified answer into a verified one. It stores what
  happened and replays it exactly. There is no path here that upgrades a
  verdict, and the stored payload carries the banner the guard produced at
  the time rather than one reconstructed later.

  It must not outlive the thing that produced it. A different model, a
  different budget, a different prover produce different answers, so all of
  them are IN THE KEY. A cache that survived a configuration change would
  serve yesterday's answer for today's system and call it consistency.

  It must not make a transient failure permanent. A run that errored is not
  stored. Caching a timeout would turn one bad minute into an answer the
  user can never get past.

OFF BY DEFAULT, because it changes what the system returns and every switch
in this project stays measurable against the thing it replaces. Set
MRA_CACHE to a directory to turn it on.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import config

# The settings that change what an answer IS. Anything here belongs in the
# key: if it can alter the verdict, a cached answer produced under a
# different value is not the same answer.
def _fingerprint() -> str:
    settings = {
        "model": getattr(config, "MODEL", ""),
        "temperature": getattr(config, "TEMPERATURE", None),
        "prover": os.getenv("MRA_PROVER", ""),
        "max_attempts": getattr(config, "MAX_ATTEMPTS", None),
        "lean_backend": os.getenv("MRA_LEAN_BACKEND", ""),
        "budget_profile": os.getenv("MRA_BUDGET_PROFILE", ""),
    }
    return json.dumps(settings, sort_keys=True)


def directory() -> str:
    """Where answers are stored, or "" when caching is off."""
    return os.getenv("MRA_CACHE", "").strip()


def enabled() -> bool:
    return bool(directory())


def key(question: str) -> str:
    """A question plus everything that changes what its answer would be."""
    payload = json.dumps(
        {"question": (question or "").strip(), "config": _fingerprint()},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Cached:
    """A previously produced answer, replayed verbatim."""

    question: str
    answer: str
    verdict: str          # the status, for reporting only
    method: str
    stored_at: str


def _path(question: str) -> Path | None:
    """Where this question's answer lives, or None if it cannot.

    The mkdir is HERE and its failure is swallowed here, because both
    callers go through this function and neither should be able to raise.
    An unwritable cache directory -- a stale path, a read-only mount, a
    parent that is a file -- must degrade to having no cache, not to a
    failed request. The first version let the OSError escape `store`'s own
    try/except, which a test caught.
    """
    where = directory()
    if not where:
        return None
    folder = Path(where)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return folder / f"{key(question)}.json"


def load(question: str) -> Cached | None:
    """The stored answer for this question, or None.

    A corrupt or unreadable entry is a MISS, never an error: a cache that
    can fail a request has made the system less reliable than having no
    cache at all.
    """
    path = _path(question)
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Cached(
            question=data["question"],
            answer=data["answer"],
            verdict=data.get("verdict", ""),
            method=data.get("method", ""),
            stored_at=data.get("stored_at", ""),
        )
    except Exception:
        return None


def store(question: str, run) -> bool:
    """Keep this run's answer. Returns whether anything was written.

    REFUSES TO STORE A RUN THAT DID NOT FINISH. A run with no verdict
    crashed, timed out or was interrupted, and freezing that would turn one
    bad minute into a permanent answer. The next caller should get a fresh
    attempt, which is the behaviour without a cache at all.
    """
    path = _path(question)
    if path is None:
        return False

    verdict = getattr(run, "verdict", None)
    answer = getattr(run, "answer", "") or ""
    if verdict is None or not answer.strip():
        return False

    from datetime import datetime, timezone

    try:
        path.write_text(json.dumps({
            "question": question,
            "answer": answer,
            "verdict": getattr(verdict.status, "value", str(verdict.status)),
            "method": getattr(verdict, "method", ""),
            "stored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # Recorded so a stored answer can be explained later. NOT read
            # back on load: the key already decided that this entry belongs
            # to this configuration, and re-deriving it would be a second
            # source of truth.
            "config": json.loads(_fingerprint()),
        }, indent=2), encoding="utf-8")
        return True
    except OSError:
        # A cache that cannot write must not break the request it was
        # supposed to speed up.
        return False
