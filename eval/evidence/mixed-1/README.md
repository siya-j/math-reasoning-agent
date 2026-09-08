# Raw evidence for the mixed run, rescued from the system temp directory

## Why this directory exists

`eval/results/mixed-1.json` was OVERWRITTEN by `--resume`. That flag
reconstructs each carried-forward goal from a hand-written list of ten fields
in `scripts/evaluate_proofs.py`, and `ProofResult` has twenty-four -- so the
other fourteen fell to their dataclass defaults and were written back as
zeroes over the real run. Lost from the results file: every accepted proof's
Lean source, every attempt, every trace, and all telemetry.

The outcomes survived, so the rates in that file (proof rate, the tier
gradient, the area table) are still correct. The evidence underneath them was
not.

`harness.prove` allocates each goal a workspace with
`tempfile.mkdtemp(prefix="mathv2_")`, which is never cleaned up automatically.
Thirty-five of those, spanning the run's window on 2026-09-07 09:57-11:33,
were still present in `C:\Users\SiyaJethliya\AppData\Local\Temp`. They are
copied here byte-for-byte, with their modification times, before Windows got
around to clearing that folder.

## What is in here, and what is NOT

Each `workdirs/mathv2_*/math/proof_log.json` is the on-disk record the guard
itself reads -- the same file `finish` is checked against. Per goal it holds
the formal statement, every attempt with the compiler's verdict and output,
the kept lemmas, the remembered premises, the trace, and the budget block
(`lean_calls`, `searches`, `symbolic_calls`, `statement_checks`,
`slowest_lean`).

VERIFIED: all twenty-four accepted proofs are present here with their Lean
source, matched to the results file by statement, 24 of 24.

NOT RECOVERABLE FROM THESE FILES, because they never existed outside the
results file -- they are read off the agent's returned transcript and the
harness's own clock, not the log: `model_calls`, `input_tokens`,
`output_tokens`, `seconds`. That is roughly 10.1M input tokens of cost
accounting, and it is gone. Anything rebuilt from this directory must report
those as UNKNOWN rather than 0: `Telemetry.complete` defaults to True so that
a caller who says nothing reads as the ordinary complete case, which means a
rebuilt record left at its defaults would certify wiped data as measured. That
is the same failure in a new coat.

## Provenance

Directory names are the original `mkdtemp` names, kept deliberately: they are
the only link back to where this came from. `manifest.json` maps each one to
its goal id.
