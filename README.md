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

`pipeline/faithfulness.py` compares the numbers in a claimed solution set
against the numbers in the question. A value that appears in the check but
nowhere in the question was invented, so the verdict is downgraded to
UNKNOWN. Narrow and deterministic — it catches one observed, damaging
mistake using arithmetic rather than another language model.

## What can be verified

| Tool | Checks |
|------|--------|
| check_equality | two expressions equal for all values — derivatives, integrals, identities |
| check_numeric | a concrete arithmetic expression equals a number |
| check_primality | an integer is prime |
| solve_equation | the solutions of an equation are exactly those claimed |
| check_limit | a function tends to a claimed value at a point or at infinity |

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

34 golden cases in `eval/golden.json`.

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

### Results (qwen2.5:3b, 34 cases)

| Run | Accuracy | Soundness | Coverage |
|-----|----------|-----------|----------|
| fixed workflow (`eval/baseline_workflow.json`) | 91% | 94% | 93% |
| tool-calling agent (`eval/baseline_agent.json`) | 82% | 97% | 79% |

Model-directed control bought soundness and cost coverage at this model
size. A 3B model choosing its own tools is a harder task than filling in a
fixed schema; it sometimes calls no tool at all, or calls one eleven times.

Three failures found by evaluation, all real, all fixed:

1. **Constant of integration** — `integrate(2*x, x)` vs `x**2 + C` was
   declared FALSE by substituting a value for the unbound `C`.
2. **Invented symbol** — the model produced a meaningless name and the
   numeric checker ruled on it.
3. **Claim substitution** — asked "is 2 the only solution of x² = 4?", the
   agent checked "are the solutions 2 and −2?" instead. Every component was
   correct and the answer was still wrong.

(1) and (2) are fixed in the verifier: it now refuses ill-posed checks.
(3) cannot be fully fixed by the guard, because the guard sees only that a
check passed, not which claim it was for. Mitigations: every tool now takes
a `claim` argument, the banner prints it, and the system prompt forbids
silently correcting the user's claim.

## Adding a verifier

Implement `verifiers.base.Verifier`, add it to `VERIFIERS` in
`verifiers/__init__.py`, and expose it as a tool in `pipeline/tools.py`.
