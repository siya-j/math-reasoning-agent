"""ProofNet -> our goal format. An adapter, and nothing else.

    python -m eval.proofnet --split validation --mode formal --out eval/proofnet.json

WHICH DATASET
-------------
`UDACA/proofnet-v3-lean4`: the Lean 4 port with the six entries that cannot
parse removed (365 rows, 182 validation / 183 test). The faithful port
`UDACA/proofnet-lean4` keeps all 371 and the original split sizes; select it
with `--dataset`.

READ THIS BEFORE QUOTING ANY NUMBER FROM IT
-------------------------------------------
arXiv 2406.07222 (EMNLP 2025) reports that the published Lean 4 ports contain
118 entries with formalisation mistakes — 31.8% of the total — which is why
ProofNet# exists. The contamination runs BOTH ways: a mis-stated theorem can
be easier to prove than the intended one, so a success can be spurious as
easily as a failure can be undeserved. Until ProofNet# is obtained, every
figure produced here is "on the uncorrected Lean 4 port" and carries that
asterisk.

THE TWO MODES, AND WHY BOTH EXIST
---------------------------------
ProofNet SUPPLIES the Lean statement, so it bypasses our formalizer entirely.

    --mode formal    the agent is given the Lean theorem and must prove it.
                     Measures the PROVER. This is what published ProofNet
                     numbers measure, so it is the only mode with an external
                     reference point. `formalisation rate` stops meaning
                     "could we formalise it" and starts meaning "does this
                     benchmark row still elaborate against our Mathlib".

    --mode informal  the agent is given only the English statement and must
                     formalise AND prove. This is our own near-mathlib task.
                     It will score much lower; that is the cost of the extra
                     half of the problem, not a regression.

Neither mode changes math_v2. The difference is entirely in the goal text.

THE HEADER PROBLEM, SOLVED IN DATA
----------------------------------
Each row carries a `header` with `open` declarations — `open Complex Filter
Function Metric Finset`, `open scoped BigOperators Topology` — without which
statements using `abs`, `∑` or `𝓝` will not elaborate. Our `build_source` uses
a fixed `import Mathlib` preamble and cannot take a per-goal header.

It does not need to. `rename_goal` renames the LAST declaration, and `open`
lines are not declarations, so they ride along inside the statement text
untouched — verified, including alongside a kept auxiliary lemma. The `import
Mathlib` line is dropped from the header because the preamble already supplies
it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

DATASETS = {
    "v3": "UDACA/proofnet-v3-lean4",       # parse-clean, 365 rows
    "v1": "UDACA/proofnet-lean4",          # faithful, 371 rows
}

# `UDACA/proofnet-lean4` ships JSONL directly; v3 ships parquet, which the
# datasets-server exposes as rows over HTTP. Both are read without adding a
# dependency on `datasets` or `pyarrow`.
JSONL = "https://huggingface.co/datasets/{repo}/resolve/main/proofnet/{file}.jsonl"
ROWS = ("https://datasets-server.huggingface.co/rows"
        "?dataset={repo}&config=default&split={split}&offset={offset}&length={length}")

SPLITS = {"validation": "valid", "test": "test"}

FORMAL_TASK = (
    "Prove this Lean 4 theorem.\n\n{statement}\n\n"
    "It formalises the following claim: {informal}"
)


def strip_comment(text: str) -> str:
    """The informal statement, without its Lean doc-comment wrapper."""
    text = (text or "").strip()
    text = re.sub(r"^/-+\s*", "", text)
    text = re.sub(r"\s*-+/$", "", text.strip())
    return " ".join(text.split())


def opens(header: str) -> str:
    """The `open` lines from a row's header. `import` lines are dropped.

    The preamble already imports Mathlib; a second import is noise, and every
    other header line is an `open` that the statement genuinely needs.
    """
    lines = [
        line.rstrip()
        for line in (header or "").splitlines()
        if line.strip() and not line.strip().startswith("import ")
    ]
    return "\n".join(lines)


def statement_with_header(row: dict) -> str:
    """The Lean statement, preceded by the opens it needs to elaborate."""
    statement = (row.get("formal_statement") or "").strip()
    preamble = opens(row.get("header", ""))
    return f"{preamble}\n\n{statement}" if preamble else statement


def area_of(name: str) -> str:
    """Textbook prefix as a coarse subject label — `exercise_1_13a` -> `1`.

    ProofNet names encode the source chapter. Not a real taxonomy, but enough
    to see whether failures cluster in one part of the benchmark.
    """
    match = re.match(r"exercise_([A-Za-z]*\d+)", name or "")
    return f"proofnet {match.group(1)}" if match else "proofnet"


def to_goal(row: dict, mode: str) -> dict:
    """One ProofNet row as a goal in the shape `eval/proof_dataset` loads."""
    informal = strip_comment(row.get("informal_prefix", ""))

    if mode == "formal":
        goal = FORMAL_TASK.format(
            statement=statement_with_header(row), informal=informal or "(none given)"
        )
    else:
        goal = informal

    return {
        "id": row.get("name", ""),
        "area": area_of(row.get("name", "")),
        "goal": goal,
        "tier": "proofnet",
        # Kept for inspection and for a future statement-preservation check.
        # Never fed to the agent in informal mode.
        "note": statement_with_header(row),
    }


def read_jsonl(path: Path) -> list:
    """Rows from a local JSONL. Same shape the download produces."""
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "math-reasoning-agent"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def load_rows(dataset: str, split: str) -> list:
    """Rows for one split. JSONL when the repo has it, the rows API otherwise."""
    repo = DATASETS[dataset]

    if dataset == "v1":
        text = _fetch(JSONL.format(repo=repo, file=SPLITS[split])).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    rows, offset = [], 0
    while True:
        payload = json.loads(_fetch(ROWS.format(
            repo=repo.replace("/", "%2F"), split=split, offset=offset, length=100
        )))
        batch = payload.get("rows", [])
        rows.extend(entry["row"] for entry in batch)
        offset += len(batch)
        if not batch or offset >= payload.get("num_rows_total", 0):
            return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="v3")
    parser.add_argument("--split", choices=sorted(SPLITS), default="validation")
    parser.add_argument(
        "--mode", choices=("formal", "informal"), default="formal",
        help="formal: give the Lean statement (measures the prover). "
             "informal: English only (measures formalisation AND proving).",
    )
    parser.add_argument(
        "--input",
        help="a local .jsonl instead of downloading. Use when the network "
        "blocks huggingface.co: download proofnet/valid.jsonl by hand and "
        "point at it. The conversion is identical either way.",
    )
    parser.add_argument("--out", default="eval/proofnet.json")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    if args.input:
        rows = read_jsonl(Path(args.input))
    else:
        try:
            rows = load_rows(args.dataset, args.split)
        except Exception as exc:  # noqa: BLE001 - a download failure is not a crash
            print(f"could not fetch {DATASETS[args.dataset]} {args.split}: {exc}")
            print("\nIf the network blocks huggingface.co, download")
            print(f"  {JSONL.format(repo=DATASETS['v1'], file=SPLITS[args.split])}")
            print("by hand and re-run with --input <path>.")
            return 1

    goals = [to_goal(row, args.mode) for row in rows]
    goals = [g for g in goals if g["id"] and g["goal"].strip()]
    if args.limit:
        goals = goals[: args.limit]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(goals, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"dataset: {DATASETS[args.dataset]}  split: {args.split}  mode: {args.mode}")
    print(f"wrote {len(goals)} goals to {out}")
    print("\nNOTE: the uncorrected Lean 4 port is reported to contain ~31.8%")
    print("formalisation errors (arXiv 2406.07222). Say so with any figure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
