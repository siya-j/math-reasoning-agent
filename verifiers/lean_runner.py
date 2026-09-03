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
# `native_decide` evaluates the proposition with the COMPILER rather than the
# kernel and closes the goal on the result, which adds `Lean.ofReduceBool` to
# the proof's axioms. That is not a stricter `decide`: it trusts the compiler
# and the runtime, and Lean's own history contains proofs of False obtained
# this way. `decide` is deliberately NOT matched -- it reduces in the kernel
# and is an ordinary tactic.
#
# It belongs here for the same reason `axiom` does: this function's subject is
# "constructs that make a file compile without proving the theorem", and a
# `native_decide` proof compiles while resting on something the kernel never
# checked. FOUND BY ATTACK, not by a failing run -- see
# tests/test_soundness_attacks.py, which reported `proved` for `by
# native_decide` against every guard in the system.
_NATIVE = re.compile(r"\b(native_decide|ofReduceBool|ofReduceNat)\b")


def lean_is_available(command: str | None = None) -> bool:
    """Is the `lean` BINARY on PATH? Says nothing about whether it can run.

    Kept as-is because three scripts use it as exactly that question. See
    `lean_toolchain_works` for the stronger one, and the note there for why
    the difference turned out to matter.
    """
    return shutil.which(command or config.LEAN_COMMAND) is not None


_TOOLCHAIN_WORKS: dict = {}


def lean_toolchain_works(command: str | None = None) -> bool:
    """Can Lean actually COMPILE anything, not merely be found on PATH?

    MEASURED, and the reason this exists: on a machine where elan is
    installed but no toolchain is configured, `lean_is_available` returns True
    and every compile then fails with

        error: no default toolchain configured. run `elan default stable` ...

    So the one real-Lean test in this repo did not SKIP in that environment --
    it FAILED. Across a long working session that failure was read as
    environmental noise and deselected every time, which is how the single
    test guarding the foundation of the whole system quietly stopped running
    at all. A check that cannot run must be skipped loudly, never failed
    quietly, or it trains everyone to ignore it.

    NEITHER `which` NOR THE EXIT CODE IS SUFFICIENT, both measured here:
    `which lean` finds the elan shim, and `lean --version` under an
    unconfigured elan prints that error and still EXITS 0. So the output is
    inspected for the version banner a working Lean prints.

    Cached, because a `skipif` at module scope evaluates on every collection
    and this spawns a process.
    """
    name = command or config.LEAN_COMMAND
    if name in _TOOLCHAIN_WORKS:
        return _TOOLCHAIN_WORKS[name]

    works = False
    if shutil.which(name):
        try:
            probe = subprocess.run(
                [name, "--version"], capture_output=True, text=True, timeout=60,
            )
            works = "lean (version" in (probe.stdout or "").lower()
        except (OSError, subprocess.SubprocessError):
            works = False
    _TOOLCHAIN_WORKS[name] = works
    return works


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
    native = _NATIVE.search(source)
    if native:
        found.append(
            f"`{native.group(0)}`, which closes the goal on the COMPILER's "
            "evaluation rather than the kernel's and rests on the "
            "`Lean.ofReduceBool` axiom"
        )
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
