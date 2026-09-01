"""Literal types for tool parameters.

NO `from __future__ import annotations` (blueprint §5.1) — these annotations
must resolve by runtime type.

Blueprint §5.2: use `Literal` enums, not free strings, for anything with a
fixed vocabulary. This is how you stop the model inventing options. Every
value below is one the code already handles; a tenth outcome or a fifth
relation would be a silent no-op otherwise.
"""

from typing import Literal

# `check_inequality`. SymPy is asked whether the relation holds for ALL reals,
# so strictness matters and there is no room for "approximately".
RelationLit = Literal["<", "<=", ">", ">="]

# What `finish` may claim. Each maps to a branch in core/verdict.py, and
# `verdict.refuse` checks the claim against the recorded log before it is
# allowed through — a value not listed here could never be validated.
OutcomeLit = Literal[
    "proved",           # Lean accepted a complete proof of the goal
    "not_proved",       # tried, nothing compiled. NOT a claim of falsity
    "not_formalized",   # Lean could not elaborate the statement at all
    "verified_true",    # a computation decided it true
    "verified_false",   # a computation decided it false
    "not_verified",     # nothing deterministic settled it
    # The claim looks false or ill-posed AS STATED. A REPORT, not a verdict:
    # we cannot confirm it, but recording it stops a broken benchmark row
    # being counted as a proving failure. Measured on exercise_1_13c, where
    # the agent correctly observed that the domain is not assumed connected.
    "statement_suspect",
    # What was given was never an assertion -- a bare formula, definition or
    # law pasted with nothing attached to check. Not a failure to formalise
    # or prove; there was nothing of that kind to formalise or prove. Refused
    # if the log already shows a claim WAS found (an elaborated statement or
    # a compiled proof), so this cannot be used to discard a real claim that
    # turned out to be hard. See verdict.nonclaim_refusal.
    "not_a_claim",
]

# Which engine a claim was put to. Recorded so a report cannot blur the line
# between "a computer algebra system computed this" and "a proof assistant
# accepted a proof of this".
MethodLit = Literal["lean", "sympy", "none"]
