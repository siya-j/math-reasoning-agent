"""The proof record — what the agent actually ran, on disk.

NO `from __future__ import annotations` IN THIS FILE. Tool modules import it,
and PEP-563 stringification silently breaks `ToolRuntime` injection for
anything downstream (AGENT_BLUEPRINT.md §5.1, gotcha 1).

WHY A FILE AND NOT MEMORY
-------------------------
In the old prover, `ProofLog` lived in a Python object for the duration of one
`prove()` call. A deep agent has no such call: it is one long conversation, and
`CappedSummarizationMiddleware` compacts history once it grows. Anything the
guard needs must therefore survive both the turn boundary and summarisation.

This is the `mach_v2` lesson from the blueprint — stage outputs live as files,
not in graph state, so later stages re-read instead of re-deriving.

THE GUARD DEPENDS ON THIS FILE BEING WRITTEN BY EXECUTION
---------------------------------------------------------
Records are appended as a side effect of a tool actually running. The model
never writes here and is never asked what happened. `finish` reads this file
and nothing else, exactly as `pipeline/guard.py` read the verification log and
never the agent's prose.
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field

LOG_DIR = "math"
LOG_NAME = "proof_log.json"

# What produced a record. Kept as plain strings so the file stays readable by a
# human with no imports, and so an old log never fails to parse after a rename.
PROOF = "proof"                 # an attempt at THE GOAL
LEMMA = "lemma"                 # an attempt at a helper result
SKELETON = "skeleton"           # a decomposition, which proves nothing alone
STATEMENT_CHECK = "statement"   # does the signature elaborate at all

TRUE = "true"
FALSE = "false"
UNKNOWN = "unknown"


@dataclass
class Record:
    """One execution. `status` is the compiler's, never the model's."""

    kind: str
    statement: str = ""
    proof: str = ""
    status: str = UNKNOWN
    detail: str = ""
    at: float = field(default_factory=time.time)


def log_path(workdir: str) -> str:
    return os.path.join(workdir, LOG_DIR, LOG_NAME)


def read(workdir: str) -> dict:
    """The whole log, or an empty one. Never raises.

    A corrupt log reads as empty rather than crashing the tool: the guard's
    failure mode must be "nothing is proved", never "the agent cannot run".
    """
    try:
        with open(log_path(workdir), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"records": [], "lemmas": [], "premises": [], "trace": []}

    if not isinstance(data, dict):
        return {"records": [], "lemmas": [], "premises": [], "trace": []}
    for key in ("records", "lemmas", "premises", "trace"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


def _write(workdir: str, data: dict) -> None:
    path = log_path(workdir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def append(workdir: str, record: Record) -> dict:
    """Record one execution and return the updated log."""
    data = read(workdir)
    data["records"].append(asdict(record))
    _write(workdir, data)
    return data


def note(workdir: str, entry: str) -> None:
    """A human-readable line for the trace. Never consulted by the guard."""
    data = read(workdir)
    data["trace"].append(entry)
    _write(workdir, data)


def keep_lemma(workdir: str, declaration: str) -> dict:
    data = read(workdir)
    if declaration not in data["lemmas"]:
        data["lemmas"].append(declaration)
    _write(workdir, data)
    return data


def kept_lemmas(workdir: str) -> list:
    return list(read(workdir)["lemmas"])


def remember_premises(workdir: str, names: list) -> None:
    data = read(workdir)
    for name in names:
        if name not in data["premises"]:
            data["premises"].append(name)
    _write(workdir, data)


def records(workdir: str, kind: str = "") -> list:
    everything = read(workdir)["records"]
    return [r for r in everything if not kind or r.get("kind") == kind]


def accepted_proof(workdir: str, statement: str = "") -> dict:
    """The first accepted attempt AT THE GOAL, or {}.

    Reads only `kind == PROOF`. A kept lemma and a typechecking skeleton are
    both real progress and neither is a proof of the goal — a run that proves
    five helpers and closes nothing has proved nothing. In the old code this
    was enforced by keeping lemmas in a separate list; here it is enforced by
    the `kind` field, and it is the same rule.

    `statement`, when given, must also match. Otherwise a proof of an earlier,
    different claim in the same conversation could be offered as a proof of
    this one.
    """
    for record in records(workdir, PROOF):
        if record.get("status") != TRUE:
            continue
        if statement and record.get("statement", "").strip() != statement.strip():
            continue
        return record
    return {}


def clear(workdir: str) -> None:
    """Start a new goal. Kept explicit — nothing clears the log implicitly."""
    _write(workdir, {"records": [], "lemmas": [], "premises": [], "trace": []})
