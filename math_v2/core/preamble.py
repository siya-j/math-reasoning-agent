"""The `open` lines a goal arrives with, kept where the compiler can reach them.

WHY THIS EXISTS, MEASURED
-------------------------
`verifiers.lean_verifier.DEFAULT_PREAMBLE` is `"import Mathlib\n"` and nothing
else. ProofNet ships every one of its 182 statements with an `open` header --
`open Fintype Set Real Ideal Polynomial`, `open Filter`, `open Real` -- and
`eval/proofnet.py` says why in its own docstring: without them the statement
does not elaborate, and the fixed preamble "cannot take a per-goal header".

The header reaches the MODEL, inside the goal text. It never reached the
COMPILER. Only 43 of 702 statements the agent submitted carried an `open`
line, so the rest were compiled against `import Mathlib` alone.

Demonstrated on `exercise_2_5_30`, same statement and same Mathlib:

    without the opens   error: Function expected at card
                              but this term has type ?m.1
    with the opens      warning: declaration uses `sorry`   (it elaborates)

The failures cluster on exactly the names an `open` provides: `card` (14
occurrences), `generateFrom` (6), `End` (4), `Tendsto` (4), `finrank` (3),
`Inf`, `Sup`, `log`, `eigenspace`, `IsTopologicalBasis`. Eleven of the
seventeen bare-name arity errors in the preserved workdirs are on goals whose
OWN header would have prevented them, across six goals that between them cost
13,288,921 input tokens -- 25% of everything this project has ever spent.

WHY A FILE RATHER THAN THE LOG
------------------------------
`log.read` normalises a fixed four-key shape and `log.clear` writes the same
four keys. Adding a fifth means editing both in step, which is the
hand-maintained-list pattern that has already cost this project three
separate bugs. A goal's preamble is also not a record of what happened; it is
part of the environment the goal arrives in, fixed for the whole run.

WHY NOT ASK THE MODEL TO INCLUDE THEM
-------------------------------------
It is told. The opens are in the goal text it receives, and it carried them
43 times out of 702. A rule that lives only in prose is one the model can
decline, which is the lesson this codebase keeps relearning; making the
preamble structural removes the choice.
"""

# NO `from __future__ import annotations` HERE. Blueprint 5.1, gotcha 1: it
# stringifies annotations and breaks `ToolRuntime` injection, and the rule
# covers every module in core/ because the tool modules import them.
# `tests/test_mathv2_log_and_verdict.py` fails if one creeps back in -- as it
# did on this module's first draft.
import os
import re

# `open Foo Bar`, `open scoped BigOperators`, `open Nat in` -- any line whose
# first token is `open`. Deliberately not parsing further: whatever the
# benchmark shipped is what the statement was written against, and this file
# is not the place to second-guess it.
_OPEN_LINE = re.compile(r"^\s*open\b.*$", re.MULTILINE)

_FILENAME = "preamble.lean"

BASE = "import Mathlib\n"


def opens_in(text: str) -> str:
    """Every `open` line in a goal's text, in order, deduplicated.

    Order is preserved because `open scoped` and plain `open` are not
    interchangeable and the benchmark's own ordering is the tested one.
    """
    seen: set = set()
    kept: list = []
    for line in _OPEN_LINE.findall(text or ""):
        stripped = line.strip()
        if stripped not in seen:
            seen.add(stripped)
            kept.append(stripped)
    return "\n".join(kept)


def _path(workdir: str) -> str:
    return os.path.join(workdir, _FILENAME)


def remember(workdir: str, goal_text: str) -> str:
    """Store the goal's `open` lines for the rest of the run. Never raises.

    A workdir that cannot be written is not a reason to fail the goal: the
    run simply compiles against the base preamble, exactly as before.
    """
    lines = opens_in(goal_text)
    try:
        with open(_path(workdir), "w", encoding="utf-8") as handle:
            handle.write(lines)
    except OSError:
        pass
    return lines


def source(workdir: str) -> str:
    """The preamble every compile in this goal should use.

    Falls back to `BASE` alone, which is the behaviour before this module
    existed -- so a missing or unreadable file costs the opens, never the run.
    """
    try:
        with open(_path(workdir), encoding="utf-8") as handle:
            lines = handle.read().strip()
    except OSError:
        return BASE
    return f"{BASE}{lines}\n" if lines else BASE
