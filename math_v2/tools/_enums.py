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
]

# Which engine a claim was put to. Recorded so a report cannot blur the line
# between "a computer algebra system computed this" and "a proof assistant
# accepted a proof of this".
MethodLit = Literal["lean", "sympy", "none"]
