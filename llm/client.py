"""Builds the chat model. The only place a provider is chosen."""

from langchain.chat_models import init_chat_model

import config

# Which environment variable each provider expects. Used only to turn a
# provider's validation error into a message that says what to do about it.
_KEYS = {
    "google_genai": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": None,  # local, no key
}


class ModelUnavailable(RuntimeError):
    """The configured model could not be built. Says which knob to turn."""


def get_model():
    """Return a LangChain chat model built from config.py."""
    try:
        return init_chat_model(config.MODEL, temperature=config.TEMPERATURE)
    except Exception as exc:
        provider = config.MODEL.split(":", 1)[0]
        key = _KEYS.get(provider, f"{provider.upper()}_API_KEY")
        hint = (
            f"\n  export {key}=...        # the provider's key"
            if key
            else "\n  (this provider is local and needs no key)"
        )
        raise ModelUnavailable(
            f"Could not build model {config.MODEL!r}.\n"
            f"  export MRA_MODEL=...     # choose a different model{hint}\n"
            f"Original error: {exc}"
        ) from exc
