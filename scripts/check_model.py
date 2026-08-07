"""Check that the configured model can actually drive the agent.

Run this BEFORE a long eval run, especially after switching providers. The
fragile part with small local models is TOOL CALLING: they can chat fine and
still never call a tool, which would silently produce unverified answers.

    python scripts/check_model.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import pipeline  # noqa: E402
from llm import get_model  # noqa: E402

SAMPLE = "Is 7919 a prime number?"


def main() -> int:
    print(f"Model: {config.MODEL}\n")

    try:
        model = get_model()
    except Exception as exc:
        print(f"[FAIL] could not build the model: {exc}")
        return 1

    try:
        text = model.invoke("Reply with the single word: ready").text
        print(f"[ok]   plain call     -> {text.strip()[:60]!r}")
    except Exception as exc:
        print(f"[FAIL] plain call     -> {exc}")
        return 1

    try:
        state = pipeline.run(SAMPLE)
    except Exception as exc:
        print(f"[FAIL] agent run      -> {type(exc).__name__}: {exc}")
        # Provider errors are often wrapped several layers deep, and the
        # outermost message ("Provider returned error") says nothing useful.
        cause = exc.__cause__ or exc.__context__
        depth = 0
        while cause is not None and depth < 5:
            print(f"        caused by     -> {type(cause).__name__}: {cause}")
            for attribute in ("response", "body", "json_body", "error"):
                payload = getattr(cause, attribute, None)
                if payload is not None:
                    text = getattr(payload, "text", payload)
                    print(f"        {attribute:<13} -> {str(text)[:400]}")
            cause = cause.__cause__ or cause.__context__
            depth += 1
        print("\nIf this mentions tools or function calling, the model cannot")
        print("drive the agent. Try another id: python scripts/probe_models.py")
        return 1

    if not state.checks:
        print("[FAIL] tool calling   -> the agent answered without calling any tool.")
        print("       This model cannot be trusted for verification. Try a model")
        print("       with tool-calling support, e.g. ollama:qwen2.5.")
        return 1

    print(f"[ok]   tool calling   -> {len(state.checks)} check(s)")
    for check in state.checks:
        print(f"                        {check.summary()}")
    print(f"[--]   verdict        -> {state.verdict.status.value}")

    print("\nModel is usable. A 'true' verdict above means the whole chain works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
