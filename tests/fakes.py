"""A fake chat model, so the pipeline can be tested with no API key.

Only the MODEL is faked. The verifier is the real SymPy one, so these tests
exercise real verification behaviour against our real control flow.
"""

from types import SimpleNamespace

from llm.decomposer import _AuxiliaryClaim, _Decomposition
from llm.formalizer import _FormalizedCheck
from llm.interpreter import _InterpretedClaim

# Formal checks with known verifier outcomes.
GOOD = _FormalizedCheck(kind="equality", lhs="diff(x**3, x)", rhs="3*x**2")
WRONG = _FormalizedCheck(kind="numeric", lhs="2 + 2", rhs="5")
BROKEN = _FormalizedCheck(kind="equality", lhs="))((", rhs="1")
ABSTRACT = _FormalizedCheck(kind="none")

CLAIM = _InterpretedClaim(statement="a claim", problem_type="calculus", numbers=[])
NO_SUBCLAIMS = _Decomposition(subclaims=[])


def aux(description, **kwargs):
    """Build one auxiliary claim for a fake decomposition."""
    return _AuxiliaryClaim(description=description, **kwargs)


class FakeModel:
    """Returns queued structured objects; plain .invoke() returns filler text."""

    def __init__(self, formalizations, interpretations=None, decompositions=None):
        self.formalizations = list(formalizations)
        self.interpretations = list(interpretations or [])
        self.decompositions = list(decompositions or [])
        self.structured_calls = []

    def with_structured_output(self, schema):
        return _FakeStructured(self, schema)

    def invoke(self, prompt):
        return SimpleNamespace(text="filler text")

    def _next(self, schema):
        name = schema.__name__
        self.structured_calls.append(name)
        if name == "_InterpretedClaim":
            return self.interpretations.pop(0) if self.interpretations else CLAIM
        if name == "_Decomposition":
            return self.decompositions.pop(0) if self.decompositions else NO_SUBCLAIMS
        return self.formalizations.pop(0)


class _FakeStructured:
    def __init__(self, parent, schema):
        self.parent, self.schema = parent, schema

    def invoke(self, prompt):
        return self.parent._next(self.schema)
