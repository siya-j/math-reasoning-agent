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
           -> Formalization -> Verification   <-- retries here (Phase 4)
           -> Reasoning -> Explanation -> Final Response
```

## Reflection (Phase 4)

Formalization and verification run in a loop:

    Attempt -> Verification -> Feedback -> Improved Attempt

Only an UNKNOWN verdict triggers a retry. TRUE, FALSE and NOT_APPLICABLE all
terminate. This is deliberate: retrying on FALSE would let the formalizer
rewrite the check until the verifier agreed, turning a verifier into an
agreement machine. Attempt 2 corrects the formal check; attempt 3 re-reads
the question. `config.MAX_ATTEMPTS` bounds the loop; every attempt is kept
in `state.attempts`.

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
| 4 | Reflection and retries (AxProverBase) | done |
| 5 | Hierarchical planning / lemmas (Prover Agent) | next |
| 6 | Formal verification (Lean) — abstract mathematics | |
