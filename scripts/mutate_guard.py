"""Break the guard on purpose, and report every break no test notices.

    python scripts/mutate_guard.py            # the soundness-critical set
    python scripts/mutate_guard.py --list     # show them without running

WHAT A SURVIVOR MEANS
---------------------
Each mutation below changes a decision the system's soundness depends on. If
the suite still passes afterwards, that decision is UNTESTED: either a test is
missing, or the code is dead. Both are worth knowing and neither is visible
from a green suite.

WHY THIS EXISTS RATHER THAN mutmut OR cosmic-ray
------------------------------------------------
Two reasons, and the second is the real one. A general mutation tool over
`verdict.py`, `log.py` and `lean_runner.py` -- 829 lines -- generates
thousands of mutants, most of them uninteresting (a changed log message, a
reordered branch with identical behaviour), and takes hours. And a survivor
list nobody reads is worth nothing.

The set here is hand-picked: every entry is a change that would let the system
report a proof it does not have, or refuse one it does. That makes each
survivor immediately actionable instead of one line in a report of four
thousand.

MEASURED, and the reason this was written at all: two tests written during one
working session passed while the mechanism they existed to protect was
deleted. One asserted a flag was READ without anything checking it was
WRITTEN; the other doubled a compiler and asserted "did not compile", which
any infrastructure fault satisfies. Both were caught only by reverting the
mechanism by hand. This automates that habit for the decisions that matter
most.

HOW A SURVIVOR IS CONFIRMED
---------------------------
A fast subset runs first, because the guard's own tests take seconds. A
mutation that survives the subset is then re-checked against the WHOLE suite
before being reported -- otherwise a test living in an unexpected file would
be mistaken for a missing one, and a false survivor is as bad here as a false
alarm anywhere else in this repo.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# WHERE THE ORIGINAL LIVES WHILE A MUTATION IS ON DISK.
#
# MEASURED, and it is the reason this file exists at all made into a bug: this
# script was killed by a wall-clock timeout partway through mutation 5, and
# `math_v2/core/log.py` was left holding the mutation --- `declared_goal`
# reading the FIRST statement check instead of the last. Two tests started
# failing and the cause looked like whatever had been edited most recently,
# which is the worst possible place for the search to start.
#
# `try/finally` was already there and is not enough. It does not run on
# SIGKILL, which is what a timeout sends, and it does not run on a power cut.
# So the original goes to a file BEFORE the mutation is written, and is
# recovered on the next start.
#
# IT IS SILENT WITHOUT THIS, WHICH IS THE DANGEROUS PART. A left-behind
# mutation is a live soundness hole in the working tree, and on a repo whose
# files are CRLF the whole file shows as modified in `git status` --- so it
# reads as a line-ending artefact rather than as an injected bug.
PENDING = ROOT / ".mutate_guard_pending.json"

# The test files that cover the guard, for the fast pass.
FAST = [
    "tests/test_soundness_attacks.py",
    "tests/test_mathv2_log_and_verdict.py",
    "tests/test_mathv2_earned_exits.py",
    "tests/test_mathv2_core.py",
    "tests/test_thrash_guards.py",
    "tests/test_lean_verifier.py",
    "tests/test_guard.py",
]


def _remember(path: str, original: str) -> None:
    """Stash the untouched file where a later run can find it."""
    PENDING.write_text(
        json.dumps({"path": path, "original": original}), encoding="utf-8")


def _forget() -> None:
    try:
        os.remove(PENDING)
    except OSError:
        pass


def recover() -> str:
    """Put back a mutation a killed run left behind. The file it fixed, or "".

    Called before anything else, on every run. Deliberately NOT a check that
    refuses to proceed: the repair is unambiguous -- these are the exact bytes
    read moments before the mutation was written -- and a checker that halts
    telling you to fix the repo yourself is a checker people stop running.
    """
    try:
        stash = json.loads(PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""

    path, original = stash.get("path", ""), stash.get("original", "")
    if not path or not isinstance(original, str):
        _forget()
        return ""

    with open(ROOT / path, "w", encoding="utf-8", newline="") as handle:
        handle.write(original)
    _forget()
    return path


@dataclass
class Mutation:
    path: str
    old: str
    new: str
    what: str

    def apply(self) -> str:
        target = ROOT / self.path
        # `open(..., newline=...)` rather than `Path.read_text(newline=...)`:
        # the keyword only reached the Path methods in Python 3.13, and this
        # script failed at mutation 1 of 13 on 3.10 -- a checker that cannot
        # run is a checker that reports nothing. `open` has taken it since
        # 3.0, and the semantics are identical.
        #
        # `newline=""` is load-bearing in BOTH directions and is why the
        # keyword is here at all: on read it disables universal-newline
        # translation, so `original` holds the file's real bytes and
        # `restore` puts them back unchanged rather than silently rewriting
        # a CRLF file as LF. Anchors are matched against an LF-normalised
        # copy so a mutation's `old` string need not know which it was.
        with open(target, encoding="utf-8", newline="") as handle:
            original = handle.read()
        body = original.replace("\r\n", "\n")
        if body.count(self.old) != 1:
            raise LookupError(
                f"{self.path}: anchor found {body.count(self.old)} times, "
                "expected exactly 1 -- the code moved and this mutation is "
                "stale, which is itself worth fixing"
            )
        # THE SIDECAR IS WRITTEN BEFORE THE MUTATION, never after. If the
        # process dies between these two statements the sidecar describes a
        # file that was never changed, and restoring it is a no-op -- which is
        # the safe direction to fail in.
        _remember(self.path, original)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body.replace(self.old, self.new, 1))
        return original

    def restore(self, original: str) -> None:
        with open(ROOT / self.path, "w", encoding="utf-8",
                  newline="") as handle:
            handle.write(original)
        _forget()


MUTATIONS = [
    # ---------------------------------------------------------- verdict.py
    Mutation(
        "math_v2/core/verdict.py",
        'if claim_outcome == PROVED and verdict["outcome"] != PROVED:',
        "if False:",
        "refuse() never refuses a claimed proof",
    ),
    Mutation(
        "math_v2/core/verdict.py",
        "    if claim_outcome in (VERIFIED_TRUE, VERIFIED_FALSE):",
        "    if False:",
        "a SymPy computation is allowed to stand as a proof",
    ),
    Mutation(
        "math_v2/core/verdict.py",
        "    if not statement.strip():",
        "    if False:",
        "an empty declared goal is no longer refused, so any accepted "
        "record can decide the outcome",
    ),
    # ------------------------------------------------------------- log.py
    Mutation(
        "math_v2/core/log.py",
        'if statement and record.get("statement", "").strip() != statement.strip():',
        "if False:",
        "accepted_proof stops filtering by statement: a diversion counts "
        "as the goal",
    ),
    Mutation(
        "math_v2/core/log.py",
        '    return checks[-1]["statement"] if checks else ""',
        '    return checks[0]["statement"] if checks else ""',
        "declared_goal reads the FIRST statement check instead of the last",
    ),
    # ------------------------------------------------------ lean_runner.py
    Mutation(
        "verifiers/lean_runner.py",
        "    return bool(_PLACEHOLDER.search(source))",
        "    return False",
        "the source-side `sorry`/`admit` check is dropped",
    ),
    Mutation(
        "verifiers/lean_runner.py",
        "    if _AXIOM.search(source):",
        "    if False:",
        "an `axiom` declaration is no longer flagged",
    ),
    Mutation(
        "verifiers/lean_runner.py",
        "    native = _NATIVE.search(source)",
        "    native = None",
        "`native_decide` is no longer flagged",
    ),
    Mutation(
        "verifiers/lean_runner.py",
        "    match = _SUGGESTION.search(source)",
        "    match = None",
        "suggestion tactics (`exact?`) are no longer flagged",
    ),
    # -------------------------------------------------------- proving.py
    Mutation(
        "math_v2/core/proving.py",
        "    return bool(_TRIVIAL.match(conclusion))",
        "    return False",
        "a goal whose conclusion is `True` is accepted",
    ),
    Mutation(
        "math_v2/core/proving.py",
        "        if _normalise_claim(hypothesis) == target:\n            return True",
        "        if False:\n            return True",
        "a goal that assumes its own conclusion is accepted",
    ),
    Mutation(
        "math_v2/core/proving.py",
        # THE TRAILING COMMENT LINE IS THE DISAMBIGUATOR, not decoration:
        # `try_proof` opens with a byte-identical placeholder check, so the
        # two lines alone match twice and the anchor is rejected as ambiguous.
        # It follows that this anchor breaks whenever the comment BELOW
        # try_lemma's placeholder check is edited -- which is how it went
        # stale once, when the `says_nothing` lint was added between them.
        # That is working as intended: a stale anchor is reported loudly and
        # is cheap to repoint, where a silently-still-matching anchor on
        # relocated code would test nothing and say nothing.
        "    if has_placeholder(proof):\n        return _placeholder_refusal()\n\n"
        "    # THE SAME LINT `check_statement`, `try_proof` AND `try_skeleton`",
        "    if False:\n        return _placeholder_refusal()\n\n"
        "    # THE SAME LINT `check_statement`, `try_proof` AND `try_skeleton`",
        "try_lemma accepts a proof containing `sorry`",
    ),
    Mutation(
        "math_v2/core/proving.py",
        "    unfinished = _unfinished_skeleton(workdir)\n    if unfinished:",
        "    unfinished = _unfinished_skeleton(workdir)\n    if False:",
        "the skeleton-loop guard is disabled",
    ),
]


def run(paths, quiet=True) -> bool:
    """True when the tests PASS (i.e. the mutation was NOT caught)."""
    argv = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider",
            *paths]
    done = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    if not quiet:
        print(done.stdout[-2000:])
    return done.returncode == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", action="store_true", help="show, do not run")
    args = parser.parse_args(argv)

    # BEFORE `--list`, not after: someone reaching for `--list` to work out
    # what this script does is exactly the person whose tree may be holding a
    # mutation from the run that died.
    recovered = recover()
    if recovered:
        print(f"RECOVERED {recovered} — a previous run was killed before it "
              f"could restore this file, and it was left mutated.\n")

    if args.list:
        for index, mutation in enumerate(MUTATIONS, 1):
            print(f"{index:3d}. [{mutation.path}] {mutation.what}")
        return 0

    survivors = []
    stale = []
    print(f"{len(MUTATIONS)} soundness-critical mutations\n")

    for index, mutation in enumerate(MUTATIONS, 1):
        print(f"[{index}/{len(MUTATIONS)}] {mutation.what}")
        try:
            original = mutation.apply()
        except LookupError as exc:
            print(f"          STALE ANCHOR — {exc}")
            stale.append(mutation.what)
            continue

        try:
            if not run(FAST):
                print("          caught")
                continue
            # Survived the subset. Confirm against everything before
            # reporting it -- a test in an unexpected file would otherwise
            # look like a missing test.
            print("          survived the subset; confirming on the full suite")
            if run(["tests/"]):
                print("          *** SURVIVED — this decision is untested ***")
                survivors.append(mutation)
            else:
                print("          caught (by a test outside the subset)")
        finally:
            mutation.restore(original)

    print("\n" + "=" * 66)
    if stale:
        print(f"{len(stale)} mutation(s) could not be applied; the code moved:")
        for what in stale:
            print(f"  - {what}")
    if not survivors:
        print("No survivors. Every mutated decision is covered by a test.")
        return 0 if not stale else 1

    print(f"{len(survivors)} SURVIVOR(S) — each is a soundness decision that "
          "no test verifies:\n")
    for mutation in survivors:
        print(f"  {mutation.path}")
        print(f"    {mutation.what}")
    print("\nA survivor is not necessarily a bug. It means nothing would "
          "notice if it became one.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
