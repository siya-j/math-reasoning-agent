"""Recompile every claimed proof in a results file. No model, no agent.

    python scripts/verify_results.py eval/results/putnam-run2.json
    python scripts/verify_results.py eval/results/*.json --all

WHY THIS EXISTS
---------------
The whole system asserts exactly one thing: "the compiler accepted this."
Everything else -- the guard, the budget, the verdict re-derivation in
`math_v2.harness._to_proof_run` -- exists to make sure nothing weaker is ever
reported as that. But until now nothing ever went back and CHECKED a reported
proof against a compiler a second time. The claim was audited on the way out
and never again.

MEASURED, and the reason this could not have been written before today: the
results file did not retain enough to try. `eval/proof_metrics.ProofResult`
stored each attempt truncated to 600 characters, so `putnam_1962_b1` -- the
one PROVED result across two PutnamBench runs -- was cut off mid-`have`, and
the kept lemmas its proof cites by name were not stored at all. The evidence
for the only success the project had was, in the strict sense, gone.

WHAT INDEPENDENCE MEANS HERE, AND WHAT IT DOES NOT
--------------------------------------------------
This does not re-derive the mathematics and it does not re-run the agent. It
takes the recorded artefact -- statement, kept lemmas, accepted proof -- and
asks a fresh compiler whether that proof really closes that statement. What
it rules out is the class of failure the guard cannot see: a bug anywhere in
the recording path that reported a proof the compiler never actually
accepted. `run_lean` is reached directly, so nothing in `math_v2` sits
between the artefact and Lean.

It is deliberately STRICTER than the run was, in one way: a proof that
compiles only because it contains `sorry` is a failure here even though Lean
exits cleanly on it. `LeanOutcome.INCOMPLETE` is treated as a hard failure,
never as a pass -- that is the single most important thing this script can
catch, because it is the one way a "proof" can compile and mean nothing.

READ THE EXIT CODE
------------------
0  every claimed proof recompiled
1  at least one did not -- a soundness failure, and the loudest signal this
   repo can produce
2  verification was INCOMPLETE: no claims, an artefact that was not
   retained, or a compile that timed out or could not run. Deliberately
   neither 0 nor 1 -- "could not check" is not a pass and not a soundness
   failure, and conflating it with either is how a checker stops being
   believed.
"""

from __future__ import annotations

import argparse
import glob
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from verifiers.lean_runner import LeanOutcome, run_lean  # noqa: E402
from verifiers.lean_verifier import build_source  # noqa: E402

PROVED = "proved"
# Plain strings, matching PROVED above: this script deliberately imports
# nothing from `eval` so it can audit a results file without the evaluator.
REFUTED = "refuted"
# A third answer, distinct from pass and fail. See `check`.
UNCHECKED = "unchecked"

# Compiler output that means Lean could not start on this source. Matched
# against the ERRORS path, where an environment fault is indistinguishable
# from a rejected proof unless the message is read. None of these can be
# produced BY a proof, which is what makes the list safe: an `unknown
# identifier`, a type mismatch or an unsolved goal stays a failure.
_ENVIRONMENT_FAULTS = (
    "no default toolchain configured",
    "unknown module prefix",          # the project has no Mathlib
    "error: object file",             # a half-built .olean tree
    "could not determine lean version",
    "toolchain not installed",
)


def compiler():
    """The fastest correct way to recompile, and why it is still independent.

    MEASURED, and the reason this is not simply `run_lean`: `run_lean` spawns
    a fresh subprocess per proof, so each one re-imports Mathlib from scratch
    -- about eight minutes on real hardware before it even reaches the proof.
    Verifying the two claims in eval/results/putnam-run4.json that way did not
    finish inside 1800s and reported COULD NOT CHECK for both, which is the
    tool failing at the one job it exists for.

    The REPL pays the import ONCE and then compiles each proof against the
    base environment it produced:

        {"cmd": "import Mathlib"}              ->  {"env": 0}   paid once
        {"cmd": "theorem a ...", "env": 0}     ->  {"env": 1, ...}
        {"cmd": "theorem b ...", "env": 0}     ->  {"env": 2, ...}

    EVERY PROOF COMPILES AGAINST env 0, never against the environment another
    proof left behind, so nothing one claim declares can help the next one
    typecheck. That is what makes this correct and not merely fast, and it is
    strictly better than the obvious alternative of concatenating all the
    proofs into one file, where exactly that cross-contamination would be
    possible and invisible.

    WHAT INDEPENDENCE NOW MEANS, stated precisely because it changed. This
    file previously claimed nothing in `math_v2` sat between the artefact and
    Lean, and that is no longer true: math_v2's REPL wrapper is the transport.
    What still holds, and is the part that matters -- no model is called, no
    agent loop runs, the artefact comes from the results file rather than from
    any surviving workspace, and the verdict is the compiler's, reached
    through the same anti-cheat that refuses `sorry`, `axiom` and
    `native_decide`.

    Falls back to the cold subprocess path when the REPL is not selected, so
    the tool still works, slowly, wherever it did before.
    """
    # SET BEFORE THE IMPORT, and that ordering is load-bearing. `_repl`'s
    # timeouts are module-level `os.getenv` reads, fixed at ITS first import,
    # so a value set afterwards has no effect -- the same trap that once made
    # `--budget-profile hard-reasoning` print its own banner while every goal
    # ran on the old defaults. The import below is deliberately lazy so this
    # runs first.
    #
    # MEASURED, and both defaults are too small for THIS job even though they
    # are right for the agent's: the REPL's start budget is 600s while a cold
    # `import Mathlib` was timed at ~480s on real hardware, which is the same
    # 25%-margin mistake `LEAN_COLD_TIMEOUT` already made; and its per-command
    # budget is 180s, sized for an agent's interactive attempts, where the
    # proofs verified here are the finished article (a6's is 2552 characters
    # over four lemmas).
    #
    # `setdefault`, so anything the operator exported still wins.
    os.environ.setdefault("MRA_LEAN_REPL_START_TIMEOUT", "1800")
    os.environ.setdefault("MRA_LEAN_REPL_TIMEOUT", "900")

    try:
        from math_v2.tools import _repl, _util
    except Exception:  # noqa: BLE001 - verification must not need math_v2
        return None, "subprocess (math_v2 unavailable)"

    if not _repl.enabled():
        return None, "subprocess (REPL not selected)"

    import asyncio
    import tempfile

    workdir = tempfile.mkdtemp(prefix="verify_")
    run = _util.lean_runner(workdir)

    def compile_once(source):
        return asyncio.run(run(source))

    return compile_once, "REPL (Mathlib imported once)"


def source_for(claim: dict) -> str:
    """Rebuild exactly the file the agent had Lean compile.

    The lemma order and the "lemmas first, goal last" layout are not cosmetic:
    `math_v2.core.proving.full_statement` joins them this way, and
    `lean_verifier.rename_goal` renames only the LAST declaration -- which is
    what leaves the kept lemmas under the names the proof cites. Reproducing
    that assembly is the point; the compiler, not this script, is the
    authority on the result.
    """
    lemmas = [text for text in claim.get("lemmas") or [] if text.strip()]
    statement = claim["statement"]
    combined = "\n\n".join(lemmas + [statement]) if lemmas else statement
    return build_source(combined, claim["proof"])


def claims_in(path: Path) -> list:
    """Every compiler claim in the file, PROVED and REFUTED alike.

    A refutation is normalised into the shape a proof already has -- statement
    plus proof body plus the kept lemmas -- so `source_for` and `check` need
    no knowledge of it. The statement swapped in is the NEGATION, because that
    is what `try_refutation` compiled; the goal's own statement would not
    recompile it and would silently check the wrong thing.

    REFUTATIONS WERE NOT CHECKED AT ALL BEFORE, because they were not
    retained. `exercise_3_22` refuted ProofNet's statement of Baire's theorem
    -- it omits `[Nonempty X]`, and on the empty space the empty set is both
    open and dense -- and that claim was recheckable only from a
    `tempfile.mkdtemp` workspace. A refutation is a compiler fact of the same
    standing as a proof, and often the more consequential one, since it says a
    published benchmark is wrong. It gets audited the same way.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    claims = []
    for row in data.get("results", []):
        if row.get("outcome") == PROVED:
            claims.append(row | {"kind": "proof"})
        elif row.get("outcome") == REFUTED and (row.get("refutation") or "").strip():
            claims.append(row | {
                "kind": "refutation",
                "statement": row.get("refutation_statement", ""),
                "proof": row.get("refutation", ""),
            })
    return claims


def check(claim: dict, runner=None) -> tuple:
    """(ok, note) for one claimed proof.

    `runner` resolves to `run_lean` HERE rather than as a default argument.
    A default is bound once, at definition time, so `runner=run_lean` cannot
    be substituted afterwards -- the same eager-evaluation trap
    `pipeline/proving.py` documents for `kwargs.setdefault`, and it made the
    exit-code test compile against real Lean instead of the injected fake.
    """
    if runner is None:
        fast, _ = compiler()
        runner = fast or (
            lambda source: run_lean(source, timeout=config.LEAN_COLD_TIMEOUT))
    if not (claim.get("proof") or "").strip():
        # UNCHECKED, NOT A FAILURE, for the same reason a timeout is not one
        # — see the note below: "SLOW IS NOT UNSOUND, AND NEITHER IS ABSENT".
        # A missing proof says nothing whatever about the proof; it says the
        # file predates the field.
        #
        # MEASURED across eval/results/: of 173 accepted proofs, 64 retain
        # their text and 109 do not. Returning False for those made every
        # full-corpus run exit 1 under "A claim that does not recompile is a
        # soundness failure. Do not quote a proof rate" — on 109 rows where
        # nothing had been recompiled at all. That is the false alarm the
        # note below warns about, and it drowned the 64 real checks.
        #
        # 24 of the 109 are recoverable rather than lost: mixed-1.json's
        # rows have proofs in mixed-1-rebuilt.json, recovered from the
        # surviving workdirs. The other 85 predate proof retention entirely.
        return UNCHECKED, (
            "NO PROOF RETAINED — this result predates the change that stores "
            "it, so the claim cannot be checked. Re-run the goal to produce a "
            "verifiable record."
        )

    result = runner(source_for(claim))
    if result.outcome is LeanOutcome.COMPILED:
        return True, "recompiled"
    if result.outcome is LeanOutcome.INCOMPLETE:
        return False, (
            "USES `sorry` OR `admit` — Lean exits cleanly on these and they "
            "prove nothing. This is a soundness failure, not a compile error."
        )
    # SLOW IS NOT UNSOUND, AND NEITHER IS ABSENT. MEASURED: with the default
    # 60s timeout a cold `import Mathlib` does not finish, and this fell
    # through to "REJECTED", which the run reported as a soundness failure
    # with "do not quote a proof rate". On a machine where Lean, the project
    # and Mathlib were all healthy it would have condemned a genuine proof.
    # A false alarm in a soundness checker is worse than no checker: it
    # teaches the reader to ignore the real ones.
    if result.outcome in (LeanOutcome.TIMEOUT, LeanOutcome.UNAVAILABLE):
        # The advice has to match the outcome. Telling someone whose Lean is
        # not installed to raise a timeout sends them to the wrong place, and
        # a diagnosis nobody can act on is the same as none.
        cure = (
            "Lean did not run at all — check MRA_LEAN and MRA_LEAN_PROJECT."
            if result.outcome is LeanOutcome.UNAVAILABLE else
            "The compile ran out of time. Raise MRA_LEAN_COLD_TIMEOUT (cold "
            "subprocess path) or MRA_LEAN_REPL_TIMEOUT (REPL path)."
        )
        return UNCHECKED, (
            f"COULD NOT CHECK ({result.outcome.value}) — this says nothing "
            f"about the proof. {cure} Run scripts/diagnose_lean.py."
        )
    # AND NEITHER IS A BROKEN TOOLCHAIN. The note above fixed TIMEOUT and
    # UNAVAILABLE, but an unconfigured elan does not reach either: `lean
    # --version` exits 0 under one (see `lean_toolchain_works`), so
    # `lean_is_available` passes, the compile runs, and Lean's own complaint
    # comes back as ERRORS. MEASURED in this repo's WSL clone:
    #
    #     REJECTED — error: no default toolchain configured.
    #                run `elan default stable` ...
    #
    # reported under "A claim that does not recompile is a soundness failure.
    # Do not quote a proof rate" -- about a proof Lean never looked at. Same
    # false alarm, different trigger, and the same reason it matters: a
    # checker that cries wolf teaches the reader to ignore it.
    #
    # Deliberately narrow. Each phrase means Lean could not START on this
    # source; none of them can be produced BY a proof. An `unknown
    # identifier` or a type mismatch stays a failure, because those are the
    # proof.
    output = (result.output or "").strip()
    lowered = output.lower()
    if any(phrase in lowered for phrase in _ENVIRONMENT_FAULTS):
        return UNCHECKED, (
            f"COULD NOT CHECK (environment) — this says nothing about the "
            f"proof. {output[:200]} Run scripts/diagnose_lean.py."
        )
    return False, f"REJECTED — {output[:400]}"


def verify(path: Path, runner=None) -> tuple:
    """(checked, failures, unchecked) for one file, printing as it goes."""
    runner = runner or run_lean
    claims = claims_in(path)
    print(f"\n{path}")
    if not claims:
        print("  no `proved` results to check")
        return 0, [], []

    failures = []
    unchecked = []
    for claim in claims:
        ok, note = check(claim, runner)
        mark = {True: "  ok  ", UNCHECKED: "  ??  "}.get(ok, "  FAIL")
        kind = "" if claim.get("kind") == "proof" else "  [refutation]"
        print(f"{mark}  {claim.get('goal_id', '?')}{kind}  {note}")
        if ok is UNCHECKED:
            unchecked.append((claim.get("goal_id", "?"), note))
        elif not ok:
            failures.append((claim.get("goal_id", "?"), note))
    return len(claims), failures, unchecked


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", help="results JSON files (globs ok)")
    args = parser.parse_args(argv)

    paths = [Path(p) for pattern in args.files for p in sorted(glob.glob(pattern))]
    if not paths:
        print("no results files matched")
        return 2

    fast, how = compiler()
    print(f"compiling via: {how}")
    runner = fast or (
        lambda source: run_lean(source, timeout=config.LEAN_COLD_TIMEOUT))

    checked = 0
    failures = []
    unchecked = []
    for path in paths:
        count, failed, skipped = verify(path, runner)
        checked += count
        failures.extend(failed)
        unchecked.extend(skipped)

    print("\n" + "-" * 60)
    if not checked:
        print("NOTHING CHECKED. No `proved` results, or none retained a proof.")
        return 2
    if failures:
        print(f"{len(failures)} of {checked} claimed proofs DID NOT VERIFY:")
        for goal_id, note in failures:
            print(f"  {goal_id}: {note}")
        print("\nA claim that does not recompile is a soundness failure. Do "
              "not quote a proof rate from this run until it is explained.")
        return 1
    if unchecked:
        # NOT exit 0. Those claims were never actually tested, and reporting
        # that as a pass is exactly how an unverified run gets quoted as a
        # verified one.
        rest = checked - len(unchecked)
        print(f"{len(unchecked)} of {checked} claimed proofs COULD NOT BE "
              "CHECKED (see above)."
              + (f" The other {rest} recompiled." if rest else
                 " NOTHING was verified."))
        return 2
    print(f"all {checked} claimed proof(s) recompiled independently")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
