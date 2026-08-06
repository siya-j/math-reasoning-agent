"""Builds the chat model. The only place a provider is chosen."""

from langchain.chat_models import init_chat_model

import config


def get_model():
    """Return a LangChain chat model built from config.py."""
    return init_chat_model(config.MODEL, temperature=config.TEMPERATURE)
