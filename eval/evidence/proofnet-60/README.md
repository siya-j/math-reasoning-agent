# Raw evidence for the proofnet-60 run, stopped by a spending cap

73 per-goal workspaces copied byte-for-byte out of the Windows temp directory,
verified identical 73 of 73. More workspaces than goals because the run was
started four times: twice against a placeholder API key, once interrupted by
the machine sleeping, and once for real.

## What the run actually produced

Eleven decided ProofNet goals -- 7 proved, 3 not proved, 1 refuted -- before
Google returned `429 RESOURCE_EXHAUSTED: Your project has exceeded its monthly
spending cap`. All eight compiler claims in it recompile independently from
the results file alone, `exercise_4_5_25 [refutation]` included.

## Why four starts

Each failure taught something and none of them cost tokens:

  * A placeholder key (13 characters, "your-key-here") let the model BUILD and
    fail at call time, inside the harness's own exception handling. The
    "3 consecutive errors" abort only counted exceptions `prove()` RAISED, so
    all 53 goals ran. Fixed; the next attempt stopped after 3.
  * The machine slept overnight. `budget.elapsed` reads `time.time()`, which
    counts sleep -- deliberately, since the budget lives in a file -- so two
    goals burned a 3780s deadline while nothing ran, and were recorded
    `not_formalized`. Fixed: a run that never declared a statement and did not
    finish is EXHAUSTED, not a formalisation failure.
  * The spending cap arrives as the same class and status as a per-minute rate
    limit, so the retry backed off twice per goal -- 132 seconds waiting for a
    wall that was not moving. Fixed: exhaustion that waiting cannot resolve
    now fails fast.

## Provenance

commit ab60e54 onward, model google_genai:gemini-3.5-flash, profile
hard-reasoning, shuffled with seed 20260101, MRA_MAX_OUTPUT_TOKENS=4096,
Lean 4.33.0 / mathlib db584cd6 via the REPL.
