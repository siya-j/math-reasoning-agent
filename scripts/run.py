"""Run the agent on one question.

    python scripts/run.py "Is the derivative of x^3 equal to 3x^2?"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline  # noqa: E402

DEFAULT_QUESTION = "Is the derivative of x^3 equal to 3x^2?"


def main() -> None:
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    state = pipeline.run(question)

    print(f"\nQUESTION\n  {state.question}")
    print(f"\nCLAIM\n  {state.claim.statement}")
    print(f"  type: {state.claim.problem_type.value}")

    print(f"\nATTEMPTS ({len(state.attempts)})")
    for attempt in state.attempts:
        print(f"  {attempt.summary()}")

    print("\nFINAL FORMALIZED CHECK")
    print(f"  kind: {state.request.kind.value}")
    print(f"  lhs:  {state.request.lhs}")
    print(f"  rhs:  {state.request.rhs}")
    if state.request.candidate:
        print(f"  candidate: {state.request.candidate}")

    print(f"\nREASONING (probabilistic)\n{state.reasoning}")

    print("\nVERIFICATION (deterministic)")
    print(f"  status: {state.verdict.status.value}")
    print(f"  method: {state.verdict.method}")
    print(f"  detail: {state.verdict.detail}")

    print(f"\nFINAL ANSWER\n{state.explanation}")

    print("\nTRACE")
    for entry in state.trace:
        print(f"  - {entry}")


if __name__ == "__main__":
    main()
