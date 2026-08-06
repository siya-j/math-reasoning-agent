"""LLM layer — probabilistic reasoning (Design Doc section 9).

The only package that imports LangChain.
"""

from llm.client import get_model
from llm.explainer import explain
from llm.formalizer import formalize, reformalize
from llm.interpreter import interpret, reinterpret
from llm.reasoner import reason

__all__ = [
    "get_model",
    "interpret",
    "reinterpret",
    "formalize",
    "reformalize",
    "reason",
    "explain",
]
