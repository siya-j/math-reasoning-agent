"""Running Lean as a subprocess. I/O only — no verdicts are decided here.

This module is deliberately separated from LeanVerifier. Everything that
touches the filesystem, the clock or another process lives here; everything
that decides what a result MEANS lives next door. That split is what makes
the policy testable on a machine with no Lean installed, which is most
machines.

Nothing in this file raises. A missing compiler, a syntax error and a
timeout are all ordinary outcomes, reported as data.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import config


class LeanOutcome(Enum):
    COMPILED = "compiled"        # accepted, and nothing was assumed or deferred
    INCOMPLETE = "incomplete"    # accepted, but uses `sorry` or `admit`
    CHEATED = "cheated"          # accepted, but only by assuming or deferring
    ERRORS = "errors"            # rejected by the compiler
    TIMEOUT = "timeout"          # took longer than the budget allows
    UNAVAILABLE = "unavailable"  # no Lean on this machine


@dataclass(frozen=True)
class LeanResult:
    outcome: LeanOutcome
    output: str = ""

    @property
    def errors(self) -> list[str]:
        """Every compiler error as a BLOCK, not a line.

        Lean puts the most useful information on the lines AFTER the error:

            Claim.lean:5:2: error: unsolved goals
            case h
            G : Type u_1
            inst✝ : Group G
            ⊢ IsCyclic G

        Collecting only lines containing `error:` discarded the goal state —
        so refinement was told "unsolved goals" without being told WHICH.
        A block runs from its header until the next diagnostic header.
        """
        blocks: list[str] = []
        current: list[str] = []

        for line in self.output.splitlines():
            header = _DIAGNOSTIC.match(line)
            if header:
                if current:
                    blocks.append("\n".join(current).rstrip())
                    current = []
                # Warnings end the previous block but start no new one.
                current = [line.rstrip()] if "error:" in line else []
            elif current:
                current.append(line.rstrip())

        if current:
            blocks.append("\n".join(current).rstrip())
        return blocks

    @property
    def goals(self) -> list[str]:
        """Remaining proof obligations, as Lean printed them (`⊢ ...`)."""
        return [
            line.strip()
            for line in self.output.splitlines()
            if line.lstrip().startswith("⊢")
        ]

    @property
    def first_error(self) -> str:
        """The first compiler error, for reporting. Lean is verbose."""
        if self.errors:
            return self.errors[0].splitlines()[0]
        return self.output.strip().splitlines()[0] if self.output.strip() else ""


# Lean 4 emits this warning when a proof leans on the `sorry` placeholder.
# The file still compiles and the exit code is still 0, so the exit code
# alone is not evidence that anything was proved.
_SORRY_MARKERS = ("declaration uses 'sorry'", "uses 'sorry'")

# Ways a file can compile while proving nothing. All four are listed in
# AxProverBase (arXiv 2602.24273) as loopholes their review system closes
# before a proof is accepted.
#
#   sorry / admit   placeholders — compile with a warning, prove nothing
#   axiom           assumes the goal instead of deriving it
#   apply? exact?   suggestion tactics; they report candidates rather than
#                   committing to a proof
# `Claim.lean:5:2: error: ...` — the start of a diagnostic. Anything after it
# and before the next one belongs to it, including the goal state.
_DIAGNOSTIC = re.compile(r"^\S*?:\d+:\d+:\s*(error|warning):")

_PLACEHOLDER = re.compile(r"\b(sorry|admit)\b")
_AXIOM = re.compile(r"^\s*axiom\s+\S", re.MULTILINE)
_SUGGESTION = re.compile(r"\b(apply|exact|rw|simp|aesop|norm_num|hint)\?")


def lean_is_available(command: str | None = None) -> bool:
    return shutil.which(command or config.LEAN_COMMAND) is not None


def _uses_placeholder(source: str, output: str) -> bool:
    """`sorry` or `admit` — the proof compiles and establishes nothing."""
    if any(marker in output for marker in _SORRY_MARKERS):
        return True
    # Belt and braces: catch it in the source too, in case a future Lean
    # version stops warning. A false positive here costs a refusal; a false
    # negative would certify an empty proof.
    return bool(_PLACEHOLDER.search(source))


def has_placeholder(source: str) -> bool:
    """`sorry` or `admit` in a proof, without asking the compiler.

    The same regex `_uses_placeholder` applies after a compile, exposed so a
    caller can decline to spend the compile at all. A skeleton is SUPPOSED to
    contain `sorry`, so this is a question, not a verdict.
    """
    return bool(_PLACEHOLDER.search(source or ""))


def cheating_devices(source: str) -> list[str]:
    """Constructs that make a file compile without proving the theorem."""
    found = []
    if _AXIOM.search(source):
        found.append("an `axiom` declaration, which assumes rather than proves")
    match = _SUGGESTION.search(source)
    if match:
        found.append(f"the suggestion tactic `{match.group(0)}`")
    return found


def run_lean(
    source: str,
    command: str | None = None,
    timeout: int | None = None,
) -> LeanResult:
    """Typecheck a Lean source file and report what happened."""
    command = command or config.LEAN_COMMAND
    timeout = timeout or config.LEAN_TIMEOUT

    if not lean_is_available(command):
        return LeanResult(LeanOutcome.UNAVAILABLE)

    # `import Mathlib` only resolves inside a Lake project that depends on it,
    # so when one is configured we go through `lake env lean` and run from
    # there. Without this, every Mathlib proof fails with "unknown module".
    project = config.LEAN_PROJECT
    if project and Path(project).is_dir():
        argv_prefix = ["lake", "env", command]
        working_directory = project
    else:
        argv_prefix = [command]
        working_directory = None

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "Claim.lean"
        path.write_text(source, encoding="utf-8")
        try:
            completed = subprocess.run(
                [*argv_prefix, str(path)],
                capture_output=True,
                text=True,
                # Lean speaks UTF-8 — its errors quote goals full of ∀, ∃, ℕ.
                # Windows defaults to cp1252 here and raises UnicodeDecodeError
                # on the first such message, turning a compiler error into a
                # crash. `replace` means a stray byte costs one character, not
                # the whole run.
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=working_directory or directory,
            )
        except subprocess.TimeoutExpired:
            return LeanResult(LeanOutcome.TIMEOUT)
        except OSError as exc:  # the binary vanished between check and call
            return LeanResult(LeanOutcome.UNAVAILABLE, str(exc))

    output = (completed.stdout or "") + (completed.stderr or "")

    if completed.returncode != 0:
        return LeanResult(LeanOutcome.ERRORS, output)
    if _uses_placeholder(source, output):
        return LeanResult(LeanOutcome.INCOMPLETE, output)
    # Order matters: a clean exit code is checked LAST against the loopholes,
    # because every one of these compiles successfully.
    devices = cheating_devices(source)
    if devices:
        return LeanResult(LeanOutcome.CHEATED, "; ".join(devices))
    return LeanResult(LeanOutcome.COMPILED, output)
