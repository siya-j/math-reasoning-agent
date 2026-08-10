"""Attempt a formal proof of a claim.

    python scripts/prove.py "Every group of prime order is cyclic"
    python scripts/prove.py "..." --dry-run     # no model, no Lean

--dry-run replaces the model and the compiler with stubs. It proves nothing;
it exists so the strategy — direct attempts, refinement, lemma generation,
synthesis — can be watched end to end on a machine with neither installed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from domain.verdict import Verdict, VerificationStatus  # noqa: E402
from pipeline.prover import lean_check, prove  # noqa: E402
from verifiers.lean_runner import lean_is_available  # noqa: E402


class StubFormalizer:
    """Plausible-looking output, so the flow can be inspected without a model."""

    def statement(self, goal):
        slug = "".join(c if c.isalnum() else "_" for c in goal.lower())[:40]
        return f"theorem {slug.strip('_')} : True"

    def sketch(self, goal):
        return "[stub] an informal proof would appear here"

    def proof(self, statement, sketch, errors="", previous=""):
        return "by stub_tactic"

    def lemmas(self, goal, count):
        return [f"[stub] auxiliary lemma {i + 1} for: {goal}" for i in range(count)]

    def synthesis(self, statement, lemmas):
        return "by stub_synthesis"


def stub_check(statement, proof):
    return Verdict(
        status=VerificationStatus.UNKNOWN,
        method="stub",
        detail="error: stub compiler accepts nothing",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claim", help="the claim to prove, in plain English")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="use stubs instead of a real model and compiler",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=None,
        help=f"lemma recursion depth (default {config.LEMMA_DEPTH})",
    )
    args = parser.parse_args()

    if args.dry_run:
        run = prove(
            args.claim,
            formalizer=StubFormalizer(),
            check=stub_check,
            depth=args.depth,
        )
    else:
        if not lean_is_available():
            print(
                "Lean was not found. Every proof will be reported UNKNOWN.\n"
                "Install Lean, set MRA_LEAN, or use --dry-run to inspect the "
                "strategy.\n",
                file=sys.stderr,
            )
        try:
            run = prove(args.claim, check=lean_check, depth=args.depth)
        except Exception as exc:  # missing key, bad model id, no network
            print(f"Could not run the prover: {exc}", file=sys.stderr)
            return 2

    print(run.report())
    print()
    print("execution trace:")
    for entry in run.trace:
        print(f"  {entry}")

    return 0 if run.proved else 1


if __name__ == "__main__":
    raise SystemExit(main())
