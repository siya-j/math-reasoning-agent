"""A worked usage example for a retrieved premise, from Mathlib's own source.

WHY THIS EXISTS
---------------
Loogle indexes DECLARATIONS — a name, a signature, a module — never how the
declaration is actually APPLIED. MEASURED, `exercise_1_18a`: search correctly
surfaced Mathlib's inner-product bracket notation (`⟪x, y⟫`) after a type
mismatch, and the model used it — but wrote it without the disambiguating
field subscript Mathlib needs (`⟪x, y⟫_ℝ`), a syntax detail no bare signature
shows. A real citation of the same name almost always gets this right,
because it had to compile. This is the "Illustrate" step DRIFT (arXiv
2510.10815) adds to autoformalization retrieval, applied here to proof and
statement repair instead.

WHERE THIS LOOKS
----------------
Not the network — there is no Loogle endpoint for "show me a use of this".
`MRA_LEAN_PROJECT`'s own Lake dependency cache already holds Mathlib's real
source (`lake exe cache get` clones it; it does not fetch only compiled
`.olean` files), so this reads that tree directly.

MEASURED cost: worst case (a name that appears nowhere) scans the whole
~8,300-file tree in about a second; a name found early stops in
milliseconds. Cheap next to a 20s+ compile, so no caching or indexing was
built for a first version — see if this is ever the slow part before adding
either.

NEVER RAISES. No project configured, the path does not exist, nothing
found, anything else at all — every case returns "". This is a nice-to-have
layered on a working system, not a new way for the agent to fail: retrieval
already promises this (`retrieval/loogle.py`'s own module docstring), and
this module makes the same promise.
"""

from __future__ import annotations

import os
from pathlib import Path

_DECLARATION_KEYWORDS = ("theorem", "lemma", "def", "abbrev", "instance", "example")
_LINE_LIMIT = 200


def _mathlib_source(project: str | None = None) -> Path | None:
    """Where Mathlib's own `.lean` source lives, or None if it cannot be found.

    `MRA_MATHLIB_SOURCE` overrides the path outright, for a Lake layout that
    does not match the `.lake/packages/mathlib` convention this assumes by
    default.
    """
    override = os.getenv("MRA_MATHLIB_SOURCE", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None

    project = (project or os.getenv("MRA_LEAN_PROJECT", "")).strip()
    if not project:
        return None
    candidate = Path(project, ".lake", "packages", "mathlib", "Mathlib")
    return candidate if candidate.is_dir() else None


def _is_declaration_line(line: str, name: str) -> bool:
    """Is this the line that DECLARES `name`, rather than one that CITES it?

    The declaration is what Loogle's own signature already showed; a
    citation is what this module exists to add.
    """
    stripped = line.strip()
    leaf = name.rsplit(".", 1)[-1]
    return any(
        stripped.startswith(f"{keyword} {target}")
        for keyword in _DECLARATION_KEYWORDS
        for target in (name, leaf)
    )


def find(name: str, project: str | None = None, max_files: int = 20000) -> str:
    """One real line of Lean citing `name`, with its file for context.

    "" when nothing is found, Mathlib's source is not available locally, or
    anything at all goes wrong — see the module docstring. Stops at the
    FIRST citation found; this is meant to be one illustrative example, not
    a survey of every use.

    Tries the name AS GIVEN first, then its bare leaf. MEASURED:
    `NNReal.sqrt_mul_le_half_add` — a real, cited lemma — was invisible to a
    search for the fully-qualified string, because Mathlib's own source
    writes it unqualified from inside `namespace NNReal ... end NNReal`,
    which is the ordinary way Mathlib is written, not an edge case. The leaf
    fallback is the same trade `diagnosis.retrieval_query` already makes for
    an unknown-identifier query: a plausible hint that still has to compile,
    not an authoritative answer.
    """
    if not name or not name.strip():
        return ""
    root = _mathlib_source(project)
    if root is None:
        return ""

    leaf = name.rsplit(".", 1)[-1]
    candidates = (name,) if leaf == name else (name, leaf)

    try:
        for scanned, path in enumerate(root.rglob("*.lean")):
            if scanned >= max_files:
                break
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for candidate in candidates:
                if candidate not in text:
                    continue
                for line in text.splitlines():
                    if candidate in line and not _is_declaration_line(line, name):
                        try:
                            rel = path.relative_to(root.parent)
                        except ValueError:
                            rel = path
                        return f"{rel}: {line.strip()[:_LINE_LIMIT]}"
    except OSError:
        return ""
    return ""
