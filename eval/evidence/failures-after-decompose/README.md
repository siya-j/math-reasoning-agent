# Raw evidence for Run A, and why it had to be rescued rather than read

## What this is

The nine per-goal workspaces from the run recorded in
`eval/results/failures-after-decompose.json`, copied byte-for-byte out of
`C:\Users\SiyaJethliya\AppData\Local\Temp` with their modification times.
Verified identical to the originals, 9 of 9.

## Why it is here and not simply readable from the results file

A results file retains the Lean source of a PROVED goal, deliberately and at
some length -- `eval/proof_metrics.ProofResult` says so: "a system whose entire
assertion is 'the compiler accepted this' must retain what the compiler
accepted."

A REFUTED goal retains nothing. `proof` is empty, `stages` carries no
refutation entry, and `scripts/verify_results.py` does not know refutations
exist. So the two strongest results this agent has produced were recheckable
only from ephemeral temp directories:

  * `exercise_5_15_refutation`  (mathv2_3apege1u)
  * `exercise_3_22_refutation`  (mathv2_o5rc8epo)

Both are ACCEPTED compilations of the negation of a ProofNet statement --
compiler facts, not reports. `exercise_3_22` states Baire's theorem without
`[Nonempty X]`, and on the empty space the empty set is both open and dense,
so the hypotheses hold vacuously while the conclusion cannot. That refutation
was independently recompiled against toolchain 4.33.0 and mathlib db584cd6:
COMPILED, VerificationStatus.TRUE, one linter warning.

This directory is the stopgap. The fix is to retain refutation source in the
results file the way proof source already is, so a future run needs no rescue.

## What is in each workspace

`math/proof_log.json` is the record the guard itself reads -- the formal
statement, every attempt with the compiler's verdict and output, kept lemmas,
remembered premises, the trace, and the budget block. `mathv2_o5rc8epo` also
shows the six-attempt path to the refutation, each attempt answering the
previous compiler error: a universe mismatch, then a missing `MetricSpace`
instance, then an unknown identifier.

Directory names are the original `mkdtemp` names, kept as provenance.
