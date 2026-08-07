"""Run the agent on one question.

    python scripts/run.py "Is 97 prime, and is the derivative of x^3 equal to 3x^2?"
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

    print(f"\nATTEMPTS ({len(state.attempts)})  — decided by the pipeline, not the model")
    for attempt in state.attempts:
        print(f"  {attempt.summary()}")
        for check in attempt.checks:
            print(f"      {check.summary()}")
            print(f"          {check.detail_line()}")

    if state.evidence:
        print(f"\nAUXILIARY EVIDENCE ({len(state.evidence)}) — evidence, NOT proof")
        for check in state.evidence:
            print(f"  {check.summary()}")

    print("\nVERDICT  — computed from the checks, not from the model")
    print(f"  status: {state.verdict.status.value}")
    print(f"  method: {state.verdict.method}")
    print(f"  detail: {state.verdict.detail}")

    print(f"\nANSWER\n{state.answer}")

    print("\nTRACE")
    for entry in state.trace:
        print(f"  - {entry}")


if __name__ == "__main__":
    main()
