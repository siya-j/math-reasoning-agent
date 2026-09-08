# Raw evidence for the 20-goal ProofNet run

The 24 per-goal workspaces behind
`eval/results/proofnet-20-after-soundness.json`, copied byte-for-byte out of
the Windows temp directory with their modification times, verified identical
24 of 24. (More workspaces than goals: a resumed or retried goal gets a fresh
one.)

## Why they are kept even though the results file is now self-sufficient

Unlike the earlier runs, this one needed no rescue: `123f0cb` had already
made the results file retain proof AND refutation source, and all eleven
claims recompile from that file alone -- `scripts/verify_results.py` reports
"all 11 claimed proof(s) recompiled independently", including
`exercise_4_8 [refutation]`.

What the file does NOT carry is the shape of the attempt: which guard refused
what, in which order, and what the compiler said each time. `refusals` gives
the counts, and these logs give the sequence. On this run that matters
because it is the first in which the guards fired at all:

  * `exercise_1_18a`  assemble_first x3, decompose_first x2 -> PROVED
  * `exercise_2_5_30` decompose_first x6, assemble_first x3 -> still failed
  * `exercise_4_5_22` assemble_first x4, decompose_first x4 -> still failed

## Provenance

commit cd7d07e, model google_genai:gemini-3.5-flash, profile hard-reasoning,
shuffled with seed 20260101, Lean 4.33.0 / mathlib db584cd6 via the REPL.
