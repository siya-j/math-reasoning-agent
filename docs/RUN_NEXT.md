# What to run next — regression, then a held-out number

Two runs, in this order. The first is the safety net; the second tells you
whether the `goal_state` fix actually changed behaviour.

Neither of these is "the result". They are checks. The real number comes from
the fresh validation goals in step 3, which you have never looked at.

---

## 0. Get the changes

```powershell
git pull origin main
```

## 1. Set the budget (same window, every run below)

These are `BENCHMARK_2026_08` — the limits the earlier experiment ran under, so
comparisons are like-for-like.

```powershell
$env:MRA_PROVER            = "math_v2"
$env:MRA_EXEC              = "local"
$env:MRA_MAX_AGENT_SECONDS = "300"
$env:MRA_MAX_AGENT_LEAN    = "8"
$env:MRA_MAX_AGENT_STEPS   = "20"
$env:MRA_MAX_AGENT_SEARCHES = "8"
$env:MRA_MAX_CONSECUTIVE_SEARCHES = "3"
```

## 2. Safety net — the seven near-Mathlib goals must stay 7/7

Run this FIRST. If it drops below 7/7 the change is a regression and nothing
else matters.

```powershell
python scripts/evaluate_proofs.py --tier near-mathlib --out eval/results/near-mathlib-after-goalstate.json
```

## 3. Regression check — the same four ProofNet goals

Not a measure of the agent. It answers one question: **did the searching
stop?**

```powershell
python scripts/evaluate_proofs.py --goals eval/proofnet-formal.json `
  --goal exercise_1_13a --goal exercise_1_13c `
  --goal exercise_1_19b --goal exercise_1_26 `
  --out eval/results/proofnet-4-after-goalstate.json
```

### How to read it

Judge it on the budget columns, NOT the proof rate. Four goals is far too few
for a rate to mean anything.

| what to compare | before | pass condition |
|---|---|---|
| searches per goal | 6, 5, 7, 5 | **≤ 3 each** |
| Lean calls per goal | 2, 3, 3, 4 | higher than before |
| `genuinely_tested` | 1 of 4 | reported separately from the rest |

If searches are still above 3, the fix did not take and we look again before
changing anything else.

## 4. The actual number — fresh validation goals

Only after 2 and 3 look right. These are goals neither of us has seen, which is
the whole point: everything above was tuned on the four, so the four can no
longer tell us anything.

```powershell
python eval/proofnet.py --dataset v3 --split validation --mode formal `
  --limit 20 --out eval/proofnet-validation-20.json

python scripts/evaluate_proofs.py --goals eval/proofnet-validation-20.json `
  --out eval/results/proofnet-validation-20.json
```

Roughly 20 goals x 300s = up to 100 minutes worst case; most will stop earlier.

**Do not read individual traces from this run to decide the next change.** That
converts it into another tuning set and you lose the held-out property. Read
the summary; if you want to debug, pull a *different* slice.

The `test` split stays untouched until there is something to write up.

---

## Why the split matters

Of the four changes made from the pilot traces, one is a tuned constant
(`SEARCH_DEADLINE_FRACTION = 0.5`) chosen because four goals looked a certain
way. The others are bug fixes — code that did not do what its own name said,
wrong on every input including the 7/7 set.

Bugs are safe to fix from any evidence. Tuned constants are not, and the only
defence is a set you did not tune on.

**Stopping rule for the tune-fix-tune cycle:** after each round, count the
changes. Mostly bug fixes -> keep going. Mostly tuned constants -> stop
changing things and run the held-out set instead.
