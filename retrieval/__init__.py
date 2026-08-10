"""Mathematical knowledge retrieval (Design Doc §14).

A third kind of component, alongside verifiers and the LLM layer. It decides
nothing and proves nothing — it answers "what is this library called?".

LeanDojo's central finding is that premise selection is the bottleneck in
formal theorem proving: a model asked to write Mathlib proofs from memory is
guessing among ~167,000 declarations. This package removes the guessing.
"""

from retrieval.loogle import LoogleSearch, Premise

__all__ = ["LoogleSearch", "Premise"]
