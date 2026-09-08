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


# WHICH KWARG EACH PROVIDER USES FOR THE OUTPUT CEILING. They do not agree,
# and this matters more than it looks: every one of these chat classes is a
# pydantic model configured `extra="ignore"`, so passing the WRONG name is
# silently dropped. A cap set with `max_tokens=4096` against `google_genai`
# does exactly nothing and reports no error -- it would read as a shipped
# feature and be worth nothing.
#
# VERIFIED against the installed classes: ChatGoogleGenerativeAI declares
# `max_output_tokens` and NOT `max_tokens`; ChatAnthropic declares
# `max_tokens` and not `max_output_tokens`.
# `tests/test_output_cap.py::test_every_mapped_name_is_a_field_the_class_declares`
# re-checks that against whatever versions are installed, so an upgrade that
# renames one fails a test instead of quietly removing the cap.
_OUTPUT_LIMIT = {
    "google_genai": "max_output_tokens",
    "anthropic": "max_tokens",
    "openai": "max_tokens",
    "openrouter": "max_tokens",
    "ollama": "num_predict",
}


def output_limit_kwarg(model: str) -> dict:
    """`{kwarg: limit}` for this provider, or `{}` for no cap.

    Empty when `config.MAX_OUTPUT_TOKENS` is 0 (the off switch) or when the
    provider is one this map does not know -- guessing a name for an unknown
    provider is the silent-no-op failure above, so it declines instead.
    """
    if config.MAX_OUTPUT_TOKENS <= 0:
        return {}
    provider = model.split(":", 1)[0]
    name = _OUTPUT_LIMIT.get(provider)
    return {name: config.MAX_OUTPUT_TOKENS} if name else {}


class ModelUnavailable(RuntimeError):
    """The configured model could not be built. Says which knob to turn."""


def get_model():
    """Return a LangChain chat model built from config.py."""
    try:
        return init_chat_model(config.MODEL, temperature=config.TEMPERATURE,
                               **output_limit_kwarg(config.MODEL))
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
