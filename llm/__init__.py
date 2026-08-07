"""LLM layer — model construction (Design Doc section 9).

The agent absorbed interpretation, formalization, reasoning and explanation
into its own loop, so this layer is now just the provider adapter.
"""

from llm.client import get_model

__all__ = ["get_model"]
