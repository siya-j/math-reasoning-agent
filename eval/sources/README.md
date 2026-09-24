# Source material

Real scientific numerical work, collected from public sources. Nothing in
here was written by this project — that is the entire point.

`eval/golden-science.json` was invented by us, and probing the machinery
against paper-grade checks failed six times out of eight. A benchmark
written by the people who wrote the verifiers tests what they already
thought of.

## What to collect

Highest signal first:

1. **Corrigenda and errata.** Published corrections for numerical, unit or
   factor errors. These are mistakes real scientists made *and that survived
   peer review* — the best available evidence about what actually goes
   wrong.
2. **arXiv v1 → v2 change notes.** Authors say plainly "corrected an error
   in Eq. 14". The diff shows which class of error reaches a preprint.
3. **Supplementary information.** Wall-to-wall calculation, in the domains
   you care about.
4. **Questions researchers asked.** Physics / Chemistry / Biology Stack
   Exchange, and Cross Validated for statistics. Filter for real research
   context rather than coursework. CC-BY-SA, so usable.

## Format

Either JSON:

```json
[{"id": "corr-1", "source": "J. Chem. Phys. 158, 129902 (2023)",
  "text": "The prefactor in Eq. 12 should read ..."}]
```

or plain text with items separated by a line of dashes, because a format
that demands JSON from someone with a PDF open will not get used:

```
Corrected Eq. 14: the prefactor should be 1/(4 pi eps0), not 1/(4 pi).
---
Given t = 2.006 +/- 0.002 s and L = 1.000 +/- 0.005 m, what is g and its
uncertainty?
---
```

## What to do with it

```
python3 scripts/triage_sources.py --items eval/sources/mine.txt --run \
    --out eval/results/triage.json
```

The number that matters is **SHAPED FOR** — the fraction that is a stated
claim this system could check as it stands, as opposed to a quantity a
researcher wants computed. A low number is a finding about the
architecture, and it is worth more than any quantity of new cases.

**Nothing here becomes a benchmark case on its own.** The triage is a draft
for a person to accept or throw away. A model choosing what our benchmark
contains would be a model deciding what we measure ourselves against.
