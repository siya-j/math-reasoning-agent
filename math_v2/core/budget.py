"""Hard limits on what one goal may spend.

NO `from __future__ import annotations` (blueprint §5.1, gotcha 1).

THE FAILURE THIS EXISTS FOR
---------------------------
Measured, not hypothetical: a near-Mathlib goal ran without terminating and had
to be interrupted by hand, leaving no proof, no verdict and no record. A second
run overran a 300s budget by 194s because a compile begun near the deadline
still ran a full Lean timeout past it.

SAME DESIGN AS `pipeline/proof_tools.Budget`, DIFFERENT STORAGE
---------------------------------------------------------------
The limits, their names and their semantics are deliberately identical to the
old Budget — this is the same system moved, not a parallel one. What changes is
where the counters live. The old Budget was a Python object owned by one
`prove()` call; a deep agent has no such call, so the counters go in the same
workspace file as the proof record and survive the turn boundary and history
compaction alike.

Wall clock uses `time.time()` rather than `time.monotonic()` for exactly that
reason: a monotonic reading is meaningless across processes.

TWO STAGES, BECAUSE A POLITE REQUEST IS NOT A GUARANTEE
-------------------------------------------------------
  1. At the limit, a tool returns STOP instead of doing work. The agent gets a
     chance to conclude cleanly and call `finish`.
  2. After a short grace the run is marked terminated, and from then on every
     tool refuses immediately.

Stage 2 is what makes termination a property of the code. Note precisely what
it guarantees: no further Lean compilation, SymPy dispatch or HTTP lookup
happens. The model may still emit tool calls, and each returns instantly with a
terminal result; bounding *those* is the framework's `tool_budget`. What is
guaranteed here is that the expensive work stops.

EACH LIMIT BOUNDS ONLY WHAT IT NAMES
------------------------------------
A spent compilation budget must not block a search — searches cost
milliseconds where a compile costs twenty seconds — and termination is still
guaranteed because `max_tool_calls` bounds everything.
"""

import os
import time

from math_v2 import _aura
from math_v2.core import log

# Same environment variable names as the old prover, so operational knowledge
# carries over unchanged.
MAX_TOOL_CALLS = int(os.getenv("MRA_MAX_AGENT_STEPS", "40"))
MAX_LEAN_CALLS = int(os.getenv("MRA_MAX_AGENT_LEAN", "12"))
MAX_SEARCHES = int(os.getenv("MRA_MAX_AGENT_SEARCHES", "12"))
MAX_CONSECUTIVE_SEARCHES = int(os.getenv("MRA_MAX_CONSECUTIVE_SEARCHES", "3"))
MAX_SYMBOLIC_CALLS = int(os.getenv("MRA_MAX_AGENT_SYMBOLIC", "20"))
MAX_SECONDS = float(os.getenv("MRA_MAX_AGENT_SECONDS", "900"))
GRACE = int(os.getenv("MRA_AGENT_GRACE", "3"))

# How much clock to hold back so a compile that starts can also finish.
#
# MEASURED FAILURE. This used to reserve `_aura.DEFAULT_TIMEOUT` (180s), the
# time ONE compile is allowed to take. At MRA_MAX_AGENT_SECONDS=300 that made
# 60% of the budget unusable: every compile was refused once 120s had elapsed.
# On num-primes-strictly-above the first `lake env lean` — a cold Mathlib
# import, roughly two minutes on Windows rather than the ~20s assumed
# everywhere — consumed exactly that window, so the agent formalised, searched
# twice, and was refused before it ever attempted a proof. Nothing about its
# mathematics was measured.
#
# Two separate quantities, conflated:
#   what one compile may TAKE     -> _aura.DEFAULT_TIMEOUT, the kill timeout
#   what we hold back to START one -> this, a typical compile
#
# The cap matters as much as the value. A reservation may never eat more than
# a quarter of the budget, whatever either number is set to, so this can never
# again silently consume the run it exists to protect.
LEAN_RESERVE_SECONDS = float(os.getenv("MRA_LEAN_RESERVE", "60"))
MAX_RESERVE_FRACTION = 0.25


def reserve():
    """Seconds held back so a started compile can finish."""
    return min(LEAN_RESERVE_SECONDS, MAX_SECONDS * MAX_RESERVE_FRACTION)

# The limits the previous agentic experiment actually ran under. Kept here so
# a comparison against its 86% is like-for-like rather than silently generous:
# the defaults above are larger, and a run at 900s/12 compiles measured against
# a run at 300s/8 would be a different experiment wearing the same name.
#
# Applied by setting the environment, never by changing the defaults:
#   MRA_MAX_AGENT_SECONDS=300  MRA_MAX_AGENT_LEAN=8
#   MRA_MAX_AGENT_STEPS=20     MRA_MAX_AGENT_SEARCHES=8
BENCHMARK_2026_08 = {
    "MRA_MAX_AGENT_SECONDS": "300",
    "MRA_MAX_AGENT_LEAN": "8",
    "MRA_MAX_AGENT_STEPS": "20",
    "MRA_MAX_AGENT_SEARCHES": "8",
    "MRA_MAX_CONSECUTIVE_SEARCHES": "3",
}

# Searching is only useful EARLY. Measured across four ProofNet goals: the
# clock is bought by model latency, not tool work — roughly 31 seconds per
# tool call — so a 300s budget buys about ten turns however high
# MAX_AGENT_STEPS is set. Searches took 23 of the 35 turns available, 66%, and
# every search after the halfway mark produced nothing usable while consuming
# the turns that compiling needed. Past this fraction of the clock, retrieval
# is refused so the remainder goes to Lean.
SEARCH_DEADLINE_FRACTION = float(os.getenv("MRA_SEARCH_DEADLINE", "0.5"))

# WHY THE CONSECUTIVE-SEARCH CAP DID NOT BIND, MEASURED
# -----------------------------------------------------
# `searches_since_compile` used to be reset by EVERY Lean call. It is a cap on
# RUNS of searching, not on searching, so each Lean call bought three more
# queries — and `check_statement` is a Lean call. The four ProofNet goals ran
# two or three statement checks each and executed 5, 5, 6 and 7 searches under
# a cap of 3, which is exactly what the code as written permits. Tested in
# isolation the cap fired at search 4 and looked correct; the isolated test
# never made a second Lean call, so it could not see this.
#
# The distinction that matters is not "did Lean run" but "is there something
# new to search AGAINST". A rejected proof returns a goal state — the next
# query can be aimed at what actually remains, which is targeted retrieval and
# is worth paying for. A statement check returns only elaborates / does not,
# and teaches you nothing about which lemma to look for. So only a call that
# returns a goal state refills the search allowance.


EXHAUSTED = "budget_exhausted"
REDIRECT = "budget_redirect"

_FIELDS = ("tool_calls", "lean_calls", "searches", "symbolic_calls",
           "searches_since_compile", "grace", "started", "reason", "terminated")


def _state(workdir):
    data = log.read(workdir)
    state = data.get("budget")
    if not isinstance(state, dict):
        state = {}
    base = {
        "tool_calls": 0, "lean_calls": 0, "searches": 0, "symbolic_calls": 0,
        "searches_since_compile": 0, "grace": GRACE, "started": time.time(),
        "reason": "", "terminated": False,
    }
    base.update({k: v for k, v in state.items() if k in _FIELDS})
    return data, base


def _save(workdir, data, state):
    data["budget"] = state
    log._write(workdir, data)


def read(workdir):
    """The budget as it stands. Read-only; safe to call from `finish`."""
    return _state(workdir)[1]


def reset(workdir):
    """Start the clock for a new goal. Explicit — nothing resets implicitly."""
    data, _ = _state(workdir)
    data["budget"] = {
        "tool_calls": 0, "lean_calls": 0, "searches": 0, "symbolic_calls": 0,
        "searches_since_compile": 0, "grace": GRACE, "started": time.time(),
        "reason": "", "terminated": False,
    }
    log._write(workdir, data)


def elapsed(workdir):
    return time.time() - read(workdir)["started"]


def remaining(workdir):
    return MAX_SECONDS - elapsed(workdir)


def _over(state, lean):
    """Which limit blocks THIS call: (kind, message). ("", "") to proceed."""
    spent = time.time() - state["started"]
    if spent > MAX_SECONDS:
        return "time", f"time budget spent ({spent:.0f}s of {MAX_SECONDS:.0f}s)"
    if state["tool_calls"] >= MAX_TOOL_CALLS:
        return "tool", f"tool budget spent ({MAX_TOOL_CALLS} calls)"
    if lean:
        if state["lean_calls"] >= MAX_LEAN_CALLS:
            return "lean", f"compilation budget spent ({MAX_LEAN_CALLS} compiles)"
        # Refusing to START a compile that cannot finish inside the budget.
        # Measured overshoot without this rule: 494s against 300s.
        if MAX_SECONDS - spent < reserve():
            return "time", (
                f"{spent:.0f}s of the {MAX_SECONDS:.0f}s budget used — under "
                f"{reserve():.0f}s left, too little to finish a compilation"
            )
    return "", ""


def _stop(state, kind, message, terminal):
    return {
        "ok": False,
        "error": EXHAUSTED,
        "limit": kind,
        "terminated": terminal,
        "message": (
            f"STOPPED: {message}. "
            + (
                "No further work will be done for this goal. Call `finish` and "
                "report what you have."
                if terminal
                else "Do not start anything new. Call `finish` and report what "
                "you have; if nothing was accepted, say so plainly."
            )
        ),
        "budget": {k: state[k] for k in
                   ("tool_calls", "lean_calls", "searches", "symbolic_calls")},
    }


def spend(workdir, *, lean=False, search=False, symbolic=False, goal_state=False):
    """Charge one tool call. Returns None to proceed, or a structured stop.

    `finish` is never charged: it is the clean exit, and refusing it would be
    the one way to guarantee a run ends with no verdict at all.

    `goal_state` marks a call that returns a goal state — a proof attempt, a
    lemma, a skeleton, the tactic ladder. Only those refill the search
    allowance, because only those give the next query something to aim at.
    """
    data, state = _state(workdir)

    if state["terminated"]:
        result = _stop(state, "terminated", state["reason"] or "budget spent", True)
        return result

    kind, message = _over(state, lean)
    if message:
        state["reason"] = message
        # The clock gets less grace than the other limits: each graced round
        # trip is spent in the very currency that ran out.
        state["grace"] = min(state["grace"], 1) if kind == "time" else state["grace"]
        state["grace"] -= 1
        terminal = state["grace"] < 0
        state["terminated"] = terminal
        _save(workdir, data, state)
        log.note(workdir, f"budget: {message}" + (" (terminated)" if terminal else ""))
        return _stop(state, kind, message, terminal)

    state["tool_calls"] += 1
    if lean:
        state["lean_calls"] += 1
    if goal_state:
        # NOT `if lean`. See the note above SEARCH_DEADLINE_FRACTION: resetting
        # on every Lean call let two statement checks buy six searches.
        state["searches_since_compile"] = 0
    if search:
        state["searches"] += 1
        state["searches_since_compile"] += 1
    if symbolic:
        state["symbolic_calls"] += 1

    redirect = None
    if search:
        left = MAX_LEAN_CALLS - state["lean_calls"]
        spent_now = time.time() - state["started"]
        if spent_now > MAX_SECONDS * SEARCH_DEADLINE_FRACTION:
            redirect = (
                f"SEARCH IS CLOSED: {spent_now:.0f}s of the {MAX_SECONDS:.0f}s "
                "budget is gone and the rest belongs to the compiler. You have "
                f"{left} compilation(s) left — use them. A rejected attempt "
                "returns the goal state, which is worth more than another "
                "query. If the whole proof is out of reach, decompose it with "
                "`try_skeleton` and `try_lemma`."
            )
        elif state["searches"] > MAX_SEARCHES:
            redirect = (
                f"ENOUGH SEARCHING: {MAX_SEARCHES} searches used. You have "
                f"{left} compilation(s) left. Work with what you have."
            )
        elif state["searches_since_compile"] > MAX_CONSECUTIVE_SEARCHES:
            redirect = (
                f"ENOUGH SEARCHING: {state['searches_since_compile'] - 1} searches "
                "in a row without compiling. Compile something — a rejected "
                "attempt returns the goal state, which tells you more than "
                f"another query. You have {left} compilation(s) left."
            )
    elif symbolic and state["symbolic_calls"] > MAX_SYMBOLIC_CALLS:
        redirect = (
            f"ENOUGH COMPUTING: {MAX_SYMBOLIC_CALLS} symbolic operations used. "
            "A computation cannot prove a theorem; report what you have."
        )

    _save(workdir, data, state)

    if redirect:
        # A redirect is CHARGED like any other call, so an agent that only ever
        # searches is still bounded by max_tool_calls. This tool is spent; the
        # run is not.
        return {"ok": False, "error": REDIRECT, "terminated": False,
                "message": redirect}
    return None


def summary(workdir):
    """What was spent, for `finish` to report."""
    state = read(workdir)
    return {
        "tool_calls": state["tool_calls"],
        "lean_calls": state["lean_calls"],
        "searches": state["searches"],
        "symbolic_calls": state["symbolic_calls"],
        "seconds": round(time.time() - state["started"], 1),
        "terminated_early": bool(state["terminated"]),
        "reason": state["reason"],
    }
