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

## What was learned collecting the first batch (2026-09-24)

**Corrigenda are the wrong layer.** 14 real corrections were fetched across
chemistry, physics, materials, environmental science, astronomy and
pharmacology. The triage sorted **zero** of them as checkable claims, and
every reason said the same thing: *"requires the underlying calculation,
which is not supplied."*

A corrigendum is a NOTICE ABOUT a calculation, not the calculation. "There
is a missing factor of 1/2 in Eq. (41)" does not contain Eq. (41). The 0%
is a collection error, not a finding about the architecture, and reporting
it as one would be the mistake this project keeps having to correct.

Keep them anyway: they are real, and they are the right KIND of evidence
about what goes wrong in published science. They are just one layer too
high.

**What does work, mechanically:**

- `arxiv.org/html/<id>` returns clean readable text for papers from late
  2023 onward. No PDF parsing. This is the reliable route.
- Searching generic phrases ("check my work", "worked example") has a poor
  hit rate. Naming a specific paper or dataset beats twenty searches.

**What does not:**

- PDFs. Three attempts failed. The stdlib regex extractor in the scratchpad
  handles neither font-subset PDFs (print-to-PDF pages) nor compressed
  object streams (most modern PDFs). Reading them properly needs `pypdf`,
  which cannot be installed here: the system Python is PEP 668
  externally-managed, and `python3 -m venv` fails because `ensurepip` is
  absent and `apt install python3.14-venv` needs sudo.

  If PDF sources matter, that apt install is the one-line unblock.

- Publisher sites (ACS, RSC) return 403 or 404 to an unauthenticated fetch.

**What the next batch needs:** sources that CARRY THEIR OWN NUMBERS --
supplementary information with worked calculations, experimental papers
reporting measured values with uncertainties, questions that include the
values. Not notices about calculations held elsewhere.
