"""Find out which model ids can actually drive the agent.

A model that advertises tool support may still be served by a provider that
rejects tool requests. The only reliable test is to send one.

    python scripts/probe_models.py openrouter:a/b:free openrouter:c/d:free
    python scripts/probe_models.py            # probes a built-in shortlist
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain.chat_models import init_chat_model  # noqa: E402

from pipeline.agent import invoke_once  # noqa: E402

SHORTLIST = [
    "openrouter:inclusionai/ling-3.0-tiny:free",
    "openrouter:poolside/laguna-s-2.1:free",
    "openrouter:poolside/laguna-xs-2.1:free",
    "openrouter:cohere/north-mini-code:free",
    "ollama:qwen2.5:3b",
]

QUESTION = "Is 7919 a prime number?"


def probe(model_id: str) -> str:
    try:
        model = init_chat_model(model_id, temperature=0)
    except Exception as exc:
        return f"cannot build   ({type(exc).__name__}: {str(exc)[:90]})"

    try:
        model.invoke("Reply with the single word: ready")
    except Exception as exc:
        return f"no plain call  ({str(exc)[:90]})"

    try:
        checks, _ = invoke_once(model, QUESTION)
    except Exception as exc:
        return f"NO TOOL CALLS  ({str(exc)[:90]})"

    if not checks:
        return "answered without calling any tool"
    return f"USABLE         ({len(checks)} check: {checks[0].summary()})"


def main() -> int:
    model_ids = sys.argv[1:] or SHORTLIST
    print(f"Probing {len(model_ids)} model(s) with a tool-calling question.\n")

    usable = []
    for model_id in model_ids:
        print(f"  {model_id}")
        verdict = probe(model_id)
        print(f"      {verdict}\n")
        if verdict.startswith("USABLE"):
            usable.append(model_id)

    print("=" * 60)
    if usable:
        print("Usable for verification:")
        for model_id in usable:
            print(f'  export MRA_MODEL="{model_id}"')
    else:
        print("None of these can drive the agent.")
    print("=" * 60)
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
