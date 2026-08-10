"""Can this model write Lean that compiles?

    python scripts/probe_lean_model.py
    MRA_MODEL="openrouter:openai/gpt-oss-20b" python scripts/probe_lean_model.py

Answers empirically what is otherwise guesswork. Each task below is trivial
for anyone who knows Mathlib — if a model cannot pass these, it will not
prove anything harder, and there is no point spending a proof budget on it.

Requires Lean (MRA_LEAN_PROJECT) and a model. Costs a handful of calls.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from llm.client import get_model  # noqa: E402
from llm.formalizer import Formalizer  # noqa: E402
from retrieval.loogle import LoogleSearch  # noqa: E402
from verifiers.lean_runner import LeanOutcome, lean_is_available  # noqa: E402
from pipeline.prover import lean_check  # noqa: E402
from domain.verdict import VerificationStatus  # noqa: E402

# Deliberately easy, and ordered by what they diagnose.
TASKS = [
    ("arithmetic", "theorem probe_one : 2 + 2 = 4"),
    ("simp on a variable", "theorem probe_two (n : Nat) : n + 0 = n"),
    ("a Mathlib name", "theorem probe_three : Nat.Prime 7"),
    ("a real lemma", "theorem probe_four (n : Nat) : ∃ p, n ≤ p ∧ Nat.Prime p"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-search", action="store_true", help="disable premise retrieval"
    )
    args = parser.parse_args()

    if not lean_is_available():
        print("Lean not found. Set MRA_LEAN_PROJECT and install Lean first.")
        return 2

    print(f"model:   {config.MODEL}")
    print(f"lean:    {config.LEAN_PROJECT or '(bare lean, no Mathlib)'}")
    print(f"search:  {'off' if args.no_search else 'Loogle'}\n")

    try:
        formalizer = Formalizer(
            model=get_model(), search=None if args.no_search else LoogleSearch()
        )
    except Exception as exc:
        print(f"Could not load the model: {exc}")
        return 2

    passed = 0
    failed = 0
    errors: list[str] = []

    for label, statement in TASKS:
        print(f"  {label:<22} ", end="", flush=True)
        try:
            sketch = formalizer.sketch(statement)
            proof = formalizer.proof(statement, sketch)
            verdict = lean_check(statement, proof)
        except Exception as exc:
            # A call that never reached the model says NOTHING about the
            # model. Counting these as failures would report "cannot write
            # Lean" for an expired key — the same mistake variance.py made
            # when it counted crashes as verdicts.
            errors.append(f"{label}: {exc}")
            print(f"ERROR    {str(exc)[:60]}")
            continue

        if verdict.status is VerificationStatus.TRUE:
            passed += 1
            print(f"PROVED   {proof.splitlines()[0][:50]}")
        else:
            failed += 1
            print(f"failed   {verdict.detail.splitlines()[-1][:60]}")

    attempted = passed + failed
    print(f"\n{passed}/{attempted} compiled ({len(errors)} never reached the model).")

    if not attempted:
        print(
            "NO CONCLUSION. Every call failed before the model saw anything, so\n"
            "this says nothing about whether the model can write Lean.\n"
            f"First error: {errors[0] if errors else 'unknown'}"
        )
        return 2

    if passed == attempted:
        print("This model can write Lean. Worth running the full prover on.")
    elif passed:
        print("Partial. Usable for in-mathlib citation, not for real proofs.")
    else:
        print("This model cannot write Lean. Do not spend a proof budget on it.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
