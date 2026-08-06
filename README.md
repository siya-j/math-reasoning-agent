# Mathematical Scientific Reasoning Agent

The LLM reasons. Deterministic systems decide mathematical correctness.

## Structure

```
domain/      Claim, VerificationRequest, Verdict, ReasoningState  (no framework code)
llm/         interpretation, formalization, reasoning, explanation (LangChain lives here)
verifiers/   base.Verifier interface + SymPy implementation + registry
pipeline/    orchestration - the only module that knows the whole workflow
scripts/     entry points
tests/       offline tests (no API key needed)
config.py    provider + model
```

## Execution flow

```
User Input -> Claim Interpretation -> Problem Classification
           -> Formalization -> Reasoning
           -> Deterministic Verification (when applicable)
           -> Explanation -> Final Response
```

## What can be verified today

| Kind | Example | Verifier |
|------|---------|----------|
| equality | derivative of x^3 is 3x^2 | SymPy |
| numeric | 2 + 2 = 4 | SymPy |
| primality | 7919 is prime | SymPy |
| solution | solutions of x^2 = 4 are 2, -2 | SymPy |
| none | every vector space has a basis | none yet — Phase 6 (Lean) |

SymPy is a Computer Algebra System. It decides computational claims. Claims in
abstract algebra, topology, functional analysis and set theory are reported as
NOT VERIFIED, honestly, until a proof assistant is integrated.

## Setup

```bash
pip install -r requirements.txt
export GOOGLE_API_KEY="your-key"
pytest
python scripts/run.py "Is the derivative of x^3 equal to 3x^2?"
```

## Adding a verifier

Implement `verifiers.base.Verifier` and add it to `VERIFIERS` in
`verifiers/__init__.py`. Nothing else changes.

## Roadmap

| Phase | What | Status |
|-------|------|--------|
| 1 | LangChain fundamentals — agent loop | done |
| 2 | Modular architecture + execution flow | done |
| 3 | Deterministic verification (SymPy) | done |
| 4 | Reflection and retries (AxProverBase) | next |
| 5 | Hierarchical planning / lemmas (Prover Agent) | |
| 6 | Formal verification (Lean) — abstract mathematics | |
