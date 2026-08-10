# Mathematical Scientific Reasoning Agent

The LLM reasons and decides what to check. Deterministic systems decide
mathematical correctness. The model never gets a vote on what counts as
verified.

**[docs/consolidation-report.md](docs/consolidation-report.md)** — architecture
history, measured results, the seven failures evaluation found, design
document compliance, and next steps.

## Structure

```
domain/      Check, Verdict, VerificationRequest, AgentRun  (no framework code)
llm/         provider adapter (the only LangChain import lives here)
verifiers/   base.Verifier interface + SymPy implementation + registry
pipeline/    the agent loop (tools.py) and the guard (agent.py)
eval/        golden dataset, scoring, regression runner
scripts/     entry points
tests/       offline tests (no API key needed)
config.py    model + limits
```

## How it works

The **pipeline owns the flow**; the **agent owns tool choice**.

```
question
  -> agent invocation        model picks tools and arguments
  -> guard                   verdict from records + faithfulness lint
  -> reflection, if needed    retry, bounded, decided in code   (Phase 4)
  -> decomposition, if still unverified   auxiliary evidence     (Phase 5)
  -> answer
```

This split exists because of a measured failure: when the loop lived inside
the agent, a small model simply chose not to iterate, and Phases 4 and 5
existed in name only (`mean checks per case` was 0.94). Retry and
decomposition are now guaranteed by the pipeline, not by model judgment.

The guarantee still comes from a **guard**:

    the agent may say anything
    -> the VERDICT is computed from the recorded tool results
    -> the answer is prefixed with a banner we generate, not the model

Aggregation rules (`pipeline/guard.py`):

| Recorded checks | Verdict |
|---|---|
| none | NOT VERIFIED — the agent answered from memory |
| any using values the question never mentions | NOT VERIFIED — faithfulness lint |
| any FALSE | FALSE — one refutation outweighs any confirmations |
| all TRUE | TRUE |
| otherwise | UNKNOWN |

### Faithfulness lint

The guard can prove a check *passed*. It cannot prove the check was a
translation of the question asked. Observed failure: given "is 2 the only
solution of x² = 4?", the agent checked "are the solutions 2 and −2?",
SymPy correctly said yes, and the system confidently answered the wrong
question.

`pipeline/faithfulness.py` compares the numbers in the model's transcription
of the claim against the numbers in the question. A value that appears in
the check but nowhere in the question was invented, so the verdict is
downgraded to UNKNOWN. Narrow and deterministic — arithmetic rather than
another language model.

Only fields that should be *transcriptions* are linted, one entry per
observed substitution: `SOLUTION.candidate`, `SERIES.rhs`,
`FACTORIZATION.rhs`. Fields like `lhs` hold the expression under test and
legitimately contain derived values; linting them produced false positives.

This is a mitigation, not a solution. The lint cannot see semantic drift
that preserves the numbers. A general fix needs back-translation — render
the formal check back into English and ask whether it matches the question —
which requires a model good enough to be trusted with that judgment.

## What can be verified

| Tool | Checks |
|------|--------|
| check_equality | two expressions equal for all values — derivatives, integrals, identities |
| check_numeric | a concrete arithmetic expression equals a number |
| check_primality | an integer is prime |
| solve_equation | the solutions of an equation are exactly those claimed |
| check_limit | a function tends to a claimed value at a point or at infinity |
| check_series | a Taylor or Maclaurin expansion matches the claim |
| check_matrix | two matrix expressions are equal |
| check_inequality | an inequality holds for *every* real value, with a counterexample if not |
| check_factorization | a product is the *prime* factorisation — right value **and** prime factors |

SymPy is a Computer Algebra System. Claims in abstract algebra, topology,
functional analysis and set theory are reported as NOT VERIFIED, honestly,
until a proof assistant is integrated (planned).

The verifier refuses to rule on ill-posed checks — an unbound constant of
integration, or a symbol the model invented. Refusing is the difference
between a limited verifier and a wrong one.

## Setup

```bash
pip install -r requirements.txt
python scripts/check_model.py                    # verifies tool calling works
python scripts/run.py "Is 7919 a prime number?"
pytest
```

## Choosing a model

One env var. Nothing else changes.

```bash
export MRA_MODEL="ollama:qwen2.5:3b"             # local, unlimited, no key
export MRA_MODEL="google_genai:gemini-3.5-flash" # + GOOGLE_API_KEY
export MRA_MODEL="anthropic:claude-sonnet-4-6"   # + ANTHROPIC_API_KEY
```

Local models are weaker at tool calling. `scripts/check_model.py` fails fast
if the model answers without calling any tool.

## Evaluation

109 golden cases in `eval/golden.json`, across ten areas. Twelve are abstract
claims no CAS can settle — they are there to check that the system refuses
rather than guesses.

```bash
python scripts/evaluate.py --limit 5
python scripts/evaluate.py
```

| Outcome | Meaning |
|---------|---------|
| correct | right conclusion |
| missed  | a verdict existed, the agent could not reach it — COVERAGE gap |
| wrong   | asserted something untrue, or claimed verification for the undecidable — SOUNDNESS failure |
| error   | the run crashed |

`missed` is acceptable. **`wrong` must stay at zero.** `evaluate.py` exits
non-zero if any appear, so it works as a regression gate.

### Results

| Architecture | Model | Cases | Tools | Accuracy | Soundness | Coverage |
|---|---|---|---|---|---|---|
| fixed workflow | qwen2.5:3b | 34 | 5 | 91% | 94% | 93% |
| tool-calling agent | qwen2.5:3b | 34 | 5 | 82% | 97% | 79% |
| hybrid | qwen2.5:7b | 87 | 5 | 95% | 99% | 95% |
| hybrid | qwen2.5:7b | 109 | 9 | 96% | 99% | 96% |

At 3B, model-directed control bought soundness and cost coverage: choosing
tools is harder than filling in a fixed schema, and the model sometimes
called no tool at all, or called one eleven times. The hybrid restored the
coverage by putting retry and decomposition back in code.

**Confounds, stated plainly.** Row 3 changes both architecture and model
size; row 4 changes both case count and tool count. Neither improvement can
be attributed to a single variable. Separating them needs the same
architecture run across models on a fixed case set.

Restraint on abstract claims has been 100% since the hybrid: zero tool calls
on all twelve claims no CAS can settle.

### Failures found by evaluation

Every bug below was found by running the eval. Code review found none of
them.

| # | Failure | Status |
|---|---|---|
| 1 | Constant of integration treated as a counterexample | fixed — verifier refuses one-sided symbols |
| 2 | Invented symbol ruled on numerically | fixed — numeric checks require a number |
| 3 | Claim substitution: "is 2 the *only* solution?" checked as "are they 2 and −2?" | mitigated |
| 4 | Docstring examples copied verbatim into unrelated questions | fixed — no literals in model-facing docstrings |
| 5 | Phases 4 and 5 silently deleted by the agent rewrite | fixed — hybrid outer loop |
| 6 | Oscillating limit reported as a definite value | fixed — `AccumBounds`/`nan` refused |
| 7 | Lowercase `i` parsed as a variable, making a true claim FALSE | fixed — symbols absent from the equation are refused |
| 8 | A deliberately wrong series silently replaced with the correct one | mitigated — lint extended to `SERIES.rhs` |

Failures 3 and 8 are the same underlying problem and are **not solved**. The
guard can prove a check passed; it cannot prove the check corresponds to the
question asked. Mitigations: every tool takes a `claim` argument, the banner
prints it, the system prompt forbids silently correcting the user, and the
faithfulness lint catches substitutions that change the numbers.

## Adding a verifier

Implement `verifiers.base.Verifier`, add it to `VERIFIERS` in
`verifiers/__init__.py`, and expose it as a tool in `pipeline/tools.py`.
