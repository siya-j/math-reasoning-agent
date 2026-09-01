"""PutnamBench -> our goal format. An adapter, and nothing else.

    python -m eval.putnam --input <local clone of trishullab/PutnamBench> --out eval/putnam.json

WHY THERE IS ONLY ONE MODE
---------------------------
ProofNet's adapter (`eval/proofnet.py`) has a real `--mode formal|informal`
choice, because ProofNet supplies BOTH an informal statement and a formal one
and either can be withheld. PutnamBench always supplies the formal Lean
statement — that IS the benchmark — so there is no informal-only task worth
having and no `--mode` flag here. Every goal this module produces is built the
same way ProofNet's `--mode formal` is: the Lean statement is given to the
agent, which still calls `check_statement` on it itself (nothing in
`math_v2/harness.py` skips that step — see `eval/proofnet.py`'s own docstring
for why the same is true there), so `formalisation rate` measures "does this
statement still elaborate against our Mathlib" and proving is what's
actually under test.

WHY `--input` IS THE PRIMARY PATH, NOT A FALLBACK
---------------------------------------------------
PutnamBench's own `informal/README.md` states its informal statements are
"available with permission from the MAA" — a real licensing constraint that
does not apply to the Lean formalisations themselves. So the default and
recommended path is a local clone:

    git clone https://github.com/trishullab/PutnamBench
    python -m eval.putnam --input PutnamBench --out eval/putnam.json

This never transmits MAA-permissioned text over this module's own network
code, and it is the inverse of ProofNet's own default (network-first, with
`--input` as the blocked-network fallback) for exactly that reason.

`--fetch-github` exists for a user who has not cloned, but fetches only the
Lean files (never `informal/putnam.json`) via GitHub's anonymous, rate-limited
(60 requests/hour) API and raw-content endpoints — impractical for the full
~672-problem set in one run. Use `--input` with a real clone for anything
beyond a handful of problems via `--limit`.

OUTPUT FILES ARE NEVER COMMITTED
---------------------------------
See `.gitignore`'s `eval/putnam*.json` rule. A generated goals file may embed
`informal/putnam.json` text (see `to_goal`), which stays out of this repo's
git history for the same licensing reason.

THE HEADER PROBLEM, SOLVED THE SAME WAY AS PROOFNET
------------------------------------------------------
A PutnamBench `.lean` file is raw source, not a pre-split data row:
`import Mathlib`, maybe `open ...` lines, a `/-- ... -/` doc comment, then
`theorem putnam_<id> : <type> := by sorry`. `eval.proofnet.opens` and
`eval.proofnet.strip_comment` are pure string operations with no ProofNet-
specific assumption baked in, so they are reused verbatim here rather than
reimplemented — the same "header rides along inside the statement text,
`rename_goal` only touches the last declaration" mechanism ProofNet already
depends on in `verifiers/lean_verifier.py` needs no changes to work here too.

THE ANSWER-HOLE SUBSET IS SKIPPED, NOT SOLVED
-------------------------------------------------
A minority of PutnamBench problems need a numeric answer substituted before
the statement is even well-formed — PutnamBench's own convention names these
with an identifier ending in `_solution` (an `abbrev ..._solution := sorry`
the theorem's own statement then references). Detected heuristically by that
identifier appearing anywhere in the header or statement; if the heuristic
ever misses a case, the resulting statement simply fails to elaborate and
`check_statement` correctly reports `not_formalized` — a loud, safe failure,
not a silent wrong one. Building the substitution logic these need is
separate, later work; v1 reports how many were skipped and their ids, never
drops them silently.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

from eval.proofnet import FORMAL_TASK, opens, strip_comment

GITHUB_API = "https://api.github.com/repos/trishullab/PutnamBench/contents/lean4/src"
RAW = "https://raw.githubusercontent.com/trishullab/PutnamBench/main/lean4/src/{name}.lean"

_THEOREM_START = re.compile(r"^[ \t]*theorem[ \t]+(\S+)", re.MULTILINE)
_DOC_COMMENT = re.compile(r"/--.*?-/", re.DOTALL)
_BY_SORRY = re.compile(r"\s*:=\s*by\s+sorry\s*$", re.DOTALL)
_BARE_SORRY = re.compile(r"\s*:=\s*sorry\s*$", re.DOTALL)
_SOLUTION_HOLE = re.compile(r"\b\w+_solution\b")


def parse_file(text: str) -> dict | None:
    """One PutnamBench `.lean` file, split into header/informal/statement.

    None when the file's shape cannot be used as a goal: no `theorem` found,
    a proof body that is not a bare `sorry`/`by sorry`, or a statement that
    references an unfilled answer hole (see the module docstring).
    """
    match = _THEOREM_START.search(text)
    if not match:
        return None
    name, theorem_start = match.group(1), match.start()

    doc_match = _DOC_COMMENT.search(text, 0, theorem_start)
    header = text[: doc_match.start() if doc_match else theorem_start]
    doc_comment = strip_comment(doc_match.group(0)) if doc_match else ""

    statement = _strip_sorry(text[theorem_start:].strip())
    if statement is None:
        return None
    if _SOLUTION_HOLE.search(header) or _SOLUTION_HOLE.search(statement):
        return None

    return {"id": name, "header": header, "doc_comment": doc_comment,
            "statement": statement}


def _strip_sorry(declaration: str) -> str | None:
    """The signature alone, ending just before `:=` -- `check_statement`'s
    own expected shape. None if the proof body is not a bare placeholder."""
    for pattern in (_BY_SORRY, _BARE_SORRY):
        if pattern.search(declaration):
            return pattern.sub("", declaration).strip()
    return None


def statement_with_header(parsed: dict) -> str:
    """The Lean statement, preceded by the `open` lines it needs to elaborate.

    Same shape as `eval.proofnet.statement_with_header` — kept as a separate
    function because the input here is an already-parsed `dict` from THIS
    module, not a HuggingFace row.
    """
    preamble = opens(parsed["header"])
    return f"{preamble}\n\n{parsed['statement']}" if preamble else parsed["statement"]


def area_of(name: str) -> str:
    """Contest year as a coarse subject label — `putnam_1985_a1` -> `putnam 1985`.

    PutnamBench names encode the year, not a subject the way ProofNet's
    chapter numbers do; grouping by year at least separates eras, which
    matters less for this benchmark than knowing which decade a failure
    clusters in.
    """
    match = re.match(r"putnam_(\d{4})_", name or "")
    return f"putnam {match.group(1)}" if match else "putnam"


def to_goal(parsed: dict, informal: dict) -> dict:
    """One parsed PutnamBench problem as a goal in the shape `eval/proof_dataset` loads."""
    statement = statement_with_header(parsed)
    entry = informal.get(parsed["id"], {})
    text = entry.get("informal_statement") or parsed.get("doc_comment") or ""
    goal = FORMAL_TASK.format(statement=statement, informal=text or "(none given)")
    return {
        "id": parsed["id"],
        "area": area_of(parsed["id"]),
        "goal": goal,
        "tier": "putnam",
        # Kept for inspection and for a future statement-preservation check.
        "note": statement,
    }


def load_informal(path: Path) -> dict:
    """`{problem_name: entry}` from a local `informal/putnam.json`.

    {} if the file is absent or unreadable — retrieval degrades quietly, the
    same promise `retrieval/loogle.py` and `retrieval/usage_examples.py` make
    elsewhere in this project. Absence is expected and common: a user who
    cloned only for the Lean statements, respecting the MAA-permission
    boundary strictly, never has this file at all.
    """
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {e["problem_name"]: e for e in entries if e.get("problem_name")}


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "math-reasoning-agent"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def fetch_from_github(limit: int | None) -> list[tuple[str, str]]:
    """`[(filename, text), ...]` fetched live. See the module docstring's
    rate-limit caveat — this is the fallback path, not the recommended one."""
    listing = json.loads(_fetch(GITHUB_API))
    names = [entry["name"] for entry in listing if entry["name"].endswith(".lean")]
    if limit:
        names = names[:limit]
    files = []
    for name in names:
        text = _fetch(RAW.format(name=name[: -len(".lean")])).decode("utf-8")
        files.append((name, text))
    return files


def read_local(input_dir: Path) -> list[tuple[str, str]]:
    src = input_dir / "lean4" / "src"
    return [(path.name, path.read_text(encoding="utf-8"))
            for path in sorted(src.glob("*.lean"))]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        help="a local clone of trishullab/PutnamBench (or any directory "
        "with the same lean4/src/, optionally informal/, layout). The "
        "recommended path — see the module docstring's licensing note.",
    )
    parser.add_argument(
        "--fetch-github", action="store_true",
        help="fetch .lean files live from GitHub instead of --input. "
        "Anonymous API rate limits (60/hour) make this impractical for the "
        "full benchmark; use --limit or --input for anything larger.",
    )
    parser.add_argument("--out", default="eval/putnam.json")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    informal = {}
    if args.input:
        input_dir = Path(args.input)
        files = read_local(input_dir)
        informal = load_informal(input_dir / "informal" / "putnam.json")
    elif args.fetch_github:
        try:
            files = fetch_from_github(args.limit)
        except Exception as exc:  # noqa: BLE001 - a download failure is not a crash
            print(f"could not fetch from GitHub: {exc}")
            print("\nClone the benchmark and use --input instead:")
            print("  git clone https://github.com/trishullab/PutnamBench")
            return 1
    else:
        print("Need --input <local clone> or --fetch-github. See the module "
              "docstring for why --input is the recommended path.")
        return 2

    parsed = [parse_file(text) for _, text in files]
    skipped = [name for (name, _), p in zip(files, parsed) if p is None]
    goals_raw = [p for p in parsed if p is not None]

    goals = [to_goal(p, informal) for p in goals_raw]
    if args.limit:
        goals = goals[: args.limit]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(goals, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {len(goals)} goals to {out}")
    if skipped:
        shown = ", ".join(s.removesuffix(".lean") for s in skipped[:10])
        more = "..." if len(skipped) > 10 else ""
        print(f"skipped {len(skipped)} problem(s) that could not be used as a "
              f"goal (an answer hole, or a non-`sorry` proof body): "
              f"{shown}{more}")
    if not informal and args.input:
        print("\nNo informal/putnam.json found under --input — goals were "
              "built with the in-file doc comment only, or \"(none given)\".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
