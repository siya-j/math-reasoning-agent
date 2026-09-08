"""A ceiling on what the model may write per call, and it must actually apply.

WHY A CAP. Output tokens are cheap alone and expensive by accumulation: the
evaluation path retains the whole transcript, so everything written is re-sent
as input on every later call.

MEASURED on eval/results/failures-after-decompose.json, output tokens per
model call:

    deep-nielsen-schreier   proved      118
    lin-vector-space-basis  proved      234
    hard-sum-odd-squares    proved      391   <- most any PROOF needed
    exercise_1_19           not_proved 2266
    exercise_1_18a          not_formal 5413
    exercise_3_22           refuted   12854   <- 49% of the run's whole cost

Reading exercise_3_22's submitted proof bodies shows what the difference is:
paragraphs of "Wait, ... Wait, ..." deliberation inside the proof argument.

WHY THE PROVIDER MAPPING IS THE RISKY PART, and the reason this file exists at
all. Providers disagree on the kwarg name -- `max_output_tokens` for Google,
`max_tokens` for Anthropic -- and every one of these chat classes is a
pydantic model configured `extra="ignore"`. Passing the wrong name is silently
dropped. A cap written as `max_tokens=4096` against `google_genai` does
nothing, raises nothing, and would have shipped looking like a feature.
"""

import config
import pytest

from llm.client import _OUTPUT_LIMIT, output_limit_kwarg


# ------------------------------------------------ the silent-no-op guard
@pytest.mark.parametrize("provider,name", sorted(_OUTPUT_LIMIT.items()))
def test_every_mapped_name_is_a_field_the_class_declares(provider, name):
    """THE test that matters. Each mapped kwarg must be a field the installed
    provider class really declares, because `extra="ignore"` means a wrong
    name is accepted and discarded rather than rejected.

    Skipped for providers that are not installed -- an absent optional
    dependency is not a defect in the mapping.
    """
    classes = {
        "google_genai": ("langchain_google_genai", "ChatGoogleGenerativeAI"),
        "anthropic": ("langchain_anthropic", "ChatAnthropic"),
        "openai": ("langchain_openai", "ChatOpenAI"),
        "ollama": ("langchain_ollama", "ChatOllama"),
    }
    if provider not in classes:
        pytest.skip(f"no class known for {provider}")
    module, attr = classes[provider]
    try:
        imported = __import__(module, fromlist=[attr])
    except ImportError:
        pytest.skip(f"{module} is not installed")

    fields = getattr(imported, attr).model_fields
    assert name in fields, (
        f"{attr} does not declare {name!r}, and its `extra` policy is "
        f"{getattr(imported, attr).model_config.get('extra')!r} -- so the cap "
        f"would be silently discarded and this run would be uncapped"
    )


def test_the_google_name_is_not_max_tokens():
    """Pinned explicitly because it is the mistake that was nearly made, and
    the installed default provider is this one."""
    assert _OUTPUT_LIMIT["google_genai"] == "max_output_tokens"


# ------------------------------------------------------- the mapping
def test_the_kwarg_matches_the_provider():
    assert output_limit_kwarg("google_genai:gemini-3.5-flash") == {
        "max_output_tokens": config.MAX_OUTPUT_TOKENS}
    assert output_limit_kwarg("anthropic:claude-sonnet-4-6") == {
        "max_tokens": config.MAX_OUTPUT_TOKENS}


def test_an_unknown_provider_gets_no_cap_rather_than_a_guess(monkeypatch):
    """Guessing a name for an unknown provider is the silent no-op above. It
    declines instead, so the run is visibly uncapped rather than invisibly
    uncapped."""
    assert output_limit_kwarg("some_new_provider:x") == {}


def test_zero_turns_the_cap_off(monkeypatch):
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 0)
    assert output_limit_kwarg("google_genai:gemini-3.5-flash") == {}


def test_the_default_clears_every_successful_goal_by_a_wide_margin():
    """391 was the most any PROVED goal spent per call in the measured run.
    A cap below that would truncate work that was succeeding; this asserts the
    headroom rather than trusting the constant."""
    assert config.MAX_OUTPUT_TOKENS >= 391 * 5, config.MAX_OUTPUT_TOKENS
    # And it must still bite on the runaways, or it buys nothing.
    assert config.MAX_OUTPUT_TOKENS < 12854, config.MAX_OUTPUT_TOKENS


def test_get_model_passes_the_cap(monkeypatch):
    """Through the real `get_model`, so a future refactor that stops
    forwarding the kwarg fails here."""
    import llm.client as client

    seen = {}

    def fake_init(model, **kwargs):
        seen.update(kwargs)
        return "model"

    monkeypatch.setattr(client, "init_chat_model", fake_init)
    monkeypatch.setattr(config, "MODEL", "google_genai:gemini-3.5-flash")
    client.get_model()

    assert seen.get("max_output_tokens") == config.MAX_OUTPUT_TOKENS, seen
