# Computational science verification, alongside the prover

The agent's core capability is **theorem proving** — Lean 4 + Mathlib, with
every accepted proof independently recompilable from the results file. This
branch extends it into physics, chemistry, biology and materials: the
numerical work a research scientist does between theorems.

Both paths share the property that makes the agent worth integrating at
all — **it refuses rather than guesses.**

The science side is the newer half and the part we expect to keep improving
after integration. The prover is the part that already works.

25 commits, 65 files, +11,148 lines. Base is `math-v2-prompt-rewrite`, not
`main`, so this is only the new work.

---

## What it does

Eight deterministic verifiers behind seventeen tools. The language model
chooses *what to check*; the verifiers decide *what is true*. A verdict is
computed from recorded tool execution and never from model prose, so the
model cannot assert its way to a verified answer.

| Verifier | Decides |
|---|---|
| `sympy` | arithmetic, algebra, calculus, primality, matrices |
| `units` | dimensional consistency; a value **with its unit** |
| `reference` | molar masses from formulae; CODATA constants |
| `plausibility` | physically impossible values — **refutes only** |
| `chemistry` | whether a reaction equation balances |
| `statistics` | binomial, normal, chi-square, Student *t* |
| `uncertainty` | error propagation through a formula |
| `lean` | theorem proving (Lean 4 + Mathlib) |

Worked example — a pendulum measurement, units and error bar derived, not
stated:

```
L = 1.000 ± 0.005 m,  T = 2.006 ± 0.002 s,  g = 4π²L/T²
  →  9.8107 ± 0.0528 meter/second**2
     L: (d/dL = 9.81/s²) × 0.005 → 0.0491 m/s²
     T: (d/dT = -9.78 m/s³) × 0.002 → 0.0196 m/s²
     Assumes: distinct variables uncorrelated.
```

## What it refuses, on purpose

This is the part to review most carefully, because it is what makes the
output usable without re-checking it.

- **An empirical question gets no answer.** "Is this reaction spontaneous?"
  "Is this dose safe?" — no calculator settles those, and it says so and
  calls no tools.
- **Plausibility can only refute.** Outside the physical domain is FALSE;
  inside it is UNDECIDED, never TRUE. A concentration of 3 mol/L is not
  correct for being possible.
- **A measured constant is not decided beyond its measurement.** A claim
  about *G* stated more finely than CODATA resolves returns UNDECIDED,
  because FALSE would assert knowledge nobody has.
- **Malformed input is refused, not computed.** A p-value from a test whose
  observed and expected totals disagree is a number with no meaning, and
  that is the most dangerous thing this system could emit.
- **No threshold is invented.** It reports *p* = 0.04 and does not call it
  significant. Where the threshold sits is a judgement about an experiment.

## Measured

**Science benchmark: 63/63.** Accuracy 100%, **soundness 100%**, restraint
on undecidable questions 100%. 63 cases across physics, chemistry, biology
and units — **16 of them state a wrong value deliberately**, and all 16 were
refuted rather than silently corrected.

**ProofNet (theorem proving): 114/156 = 73.1%**, after auditing the
denominator rather than taking it on trust. Every claimed proof is
independently recompilable from the results file.

**Test status: 2,082 passed, 3 skipped**, with `scripts/env.sh` sourced so
the Lean REPL suites actually run. The 3 failures are in
`tests/test_prover_spike.py`, which is uncommitted work in progress on the
author's machine and predates this branch.

Worth running it that way: without `env.sh` over 300 tests skip silently,
and the suite reports a healthy-looking number that has not exercised the
proving backend at all.

## Not measured, and worth knowing before integrating

- **No research scientist has used this.** The benchmark was written by us.
  Probing the machinery against paper-grade checks initially failed six
  times out of eight; those gaps are fixed and now tested, but the exercise
  showed that a benchmark we wrote tests what we already thought of.
- **It decides stated claims. Researchers have documents.** A user will
  paste a paper or ask "what's the uncertainty on this?", not hand over a
  formalized claim. Someone must do that formalization step, and today it
  is whoever writes the question. This is the main product gap, not a code
  defect.
- **Run-to-run variance is ~16% at goal level** on the proving path, so a
  single-run proof rate is not an instrument. `scripts/significance.py`
  ships for this: exact paired McNemar for two runs, paired bootstrap for
  more.

## Deployment

**The proving path** needs `elan`, Mathlib and a compiled Lean REPL binary,
and pays multi-second compiles per goal. Worth planning for early rather
than late: the REPL amortises the Mathlib import across goals, which is the
difference between roughly 39 seconds and about 1 second per compile. It
wants a warm, long-lived process rather than a cold invocation per request.

**The verification path** needs only SymPy and runs anywhere.

The two are independent — `pipeline/prover.py` imports none of the science
verifiers, and a test asserts it — so they can be provisioned on different
timelines without either blocking the other.

## What changed for integration readiness

Three blockers were closed on this branch after the initial review:

- **Consistency.** The same question asked twice was giving two answers —
  run-to-run variance is about one goal in six. `pipeline/cache.py` replays
  a stored answer keyed on the question *and* the configuration. It cannot
  upgrade a verdict, cannot outlive a config change, and cannot freeze a
  crash. Off unless `MRA_CACHE` names a directory.
- **Intake.** The system decided *stated claims*; a researcher has a
  question. `compute_value` now works a quantity out and reports it as
  **COMPUTED**, never verified, with the formula printed beside it —
  because with no claimed value, the reader is the only check the
  formalisation has.
- **A false setup error.** The REPL version check refused a working install
  over a patch-level Lean difference, announcing that its own results "are
  not usable" while goals were being proved through it.

## Open items before merge
- [ ] Decide whether `eval/results/` run artefacts belong in the repo
- [ ] Agree the integration surface — which tools Aura calls, and what
      happens to an UNDECIDED verdict in the product
- [ ] Plan the Lean runtime: a warm REPL process, not a cold compile per
      request

## Review suggestions

The interesting files are the judgement calls, not the plumbing:

- `science/domains.py` — every bound is a consequence of physics, and the
  file lists what was deliberately left **out** and why. pH is bounded at
  −2 and 16 rather than the textbook 0–14, because concentrated HCl reaches
  about −1.1.
- `science/uncertainty.py` — symbolic differentiation rather than interval
  arithmetic, so `x − x` has no error bar.
- `verifiers/assumptions.py` — an assumption can turn a false claim true, so
  every verdict carries the condition it was reached under.
- `pipeline/guard.py` — the aggregation rules, including why a
  refutation-only check's UNDECIDED is an abstention rather than a failure.
