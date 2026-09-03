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
2  nothing could be checked (no claims, or the artefact was not retained)
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verifiers.lean_runner import LeanOutcome, run_lean  # noqa: E402
from verifiers.lean_verifier import build_source  # noqa: E402

PROVED = "proved"


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
    data = json.loads(path.read_text(encoding="utf-8"))
    return [r for r in data.get("results", []) if r.get("outcome") == PROVED]


def check(claim: dict, runner=None) -> tuple:
    """(ok, note) for one claimed proof.

    `runner` resolves to `run_lean` HERE rather than as a default argument.
    A default is bound once, at definition time, so `runner=run_lean` cannot
    be substituted afterwards -- the same eager-evaluation trap
    `pipeline/proving.py` documents for `kwargs.setdefault`, and it made the
    exit-code test compile against real Lean instead of the injected fake.
    """
    runner = runner or run_lean
    if not (claim.get("proof") or "").strip():
        return False, (
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
    return False, f"REJECTED — {(result.output or '').strip()[:400]}"


def verify(path: Path, runner=None) -> tuple:
    """(checked, failures) for one results file, printing as it goes."""
    runner = runner or run_lean
    claims = claims_in(path)
    print(f"\n{path}")
    if not claims:
        print("  no `proved` results to check")
        return 0, []

    failures = []
    for claim in claims:
        ok, note = check(claim, runner)
        mark = "  ok  " if ok else "  FAIL"
        print(f"{mark}  {claim.get('goal_id', '?')}  {note}")
        if not ok:
            failures.append((claim.get("goal_id", "?"), note))
    return len(claims), failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", help="results JSON files (globs ok)")
    args = parser.parse_args(argv)

    paths = [Path(p) for pattern in args.files for p in sorted(glob.glob(pattern))]
    if not paths:
        print("no results files matched")
        return 2

    checked = 0
    failures = []
    for path in paths:
        count, failed = verify(path, run_lean)
        checked += count
        failures.extend(failed)

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
    print(f"all {checked} claimed proof(s) recompiled independently")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
