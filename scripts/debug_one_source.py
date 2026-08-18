"""One gate row, both backends, everything printed. About two minutes.

    python scripts/debug_one_source.py
    python scripts/debug_one_source.py --snippet "does not exist"
    python scripts/debug_one_source.py --isolated

ONE CANONICAL DEFINITION. The case and the compile path are IMPORTED from
`scripts/compare_lean_modes.py` — `SNIPPETS`, `MATHLIB`, `compile_all` and
`in_mode` are its objects, not copies. The previous version of this script
retyped the source, and although the string turned out to be identical, that
could not be known without checking. Now it cannot differ.

WHY THE FIRST VERSION MISSED THE FAILURE
----------------------------------------
It ran the row ALONE and both backends agreed. The gate runs it TENTH, after
nine other snippets, in one process against one live REPL session:

     1. trivial proof                     6. sorry is not a proof
     2. arithmetic                        7. an axiom is cheating
     3. a real Mathlib lemma              8. a suggestion tactic is cheating
     4. unknown identifier                9. an open header
     5. unsolved goals                   10. a stray import the model wrote  <--

The source is byte-identical either way and the routing decision is a pure
function of the source, so neither of those is the difference. What is left is
SEQUENCE: something about the state of the run by the time row 10 arrives.

So this script reproduces the sequence rather than the row. And because
session state can only affect the REPL arm — a fresh `lake env lean` has no
history — the subprocess arm still runs the row alone. That keeps it to about
two minutes instead of the gate's eight.

`--isolated` runs the row alone in both arms, which is what the previous
version did. If `--isolated` agrees and the default disagrees, the cause is
sequence, and the printed per-row trace says which earlier row did it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGET = "a stray import the model wrote"


def gate():
    """`compare_lean_modes` as a module. The single source of truth."""
    spec = importlib.util.spec_from_file_location(
        "compare_lean_modes", ROOT / "scripts" / "compare_lean_modes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["compare_lean_modes"] = module
    spec.loader.exec_module(module)
    return module


def commit():
    try:
        return subprocess.run(["git", "log", "--oneline", "-1"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return "(git unavailable)"


def block(title, text):
    print(f"\n{title}")
    print("-" * len(title))
    print(text if str(text).strip() else "(empty)")


class Trace:
    """Records which path served each source, and the raw text it returned.

    Wraps the two compile entry points rather than reimplementing them, so
    what is reported is what actually ran.
    """

    def __init__(self):
        self.rows = []

    def install(self):
        from math_v2.tools import _repl, _util

        real_subprocess = _util._subprocess_compile
        real_repl = _repl.compile_source

        async def traced_subprocess(source, workdir):
            ok, text, startup = await real_subprocess(source, workdir)
            self.rows.append(("lake env lean", source, ok, text))
            return ok, text, startup

        async def traced_repl(source, cwd=None, timeout=None):
            ok, text, startup = await real_repl(source, cwd=cwd, timeout=timeout)
            self.rows.append(("repl session", source, ok, text))
            return ok, text, startup

        _util._subprocess_compile = traced_subprocess
        _repl.compile_source = traced_repl
        return self

    def last(self):
        return self.rows[-1] if self.rows else ("(nothing ran)", "", None, "")


def run_arm(clm, backend, snippets, workdir):
    """The gate's own `in_mode` and `compile_all`. Not a reimplementation."""
    trace = Trace()

    def go():
        trace.install()          # after in_mode's reload, so it wraps the live ones
        return clm.compile_all(snippets, workdir)

    results = clm.in_mode(backend == "repl", go)
    return results, trace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snippet", default=TARGET,
                        help="substring of the gate row to debug")
    parser.add_argument("--isolated", action="store_true",
                        help="run the row alone in both arms, no preceding rows")
    args = parser.parse_args()

    os.environ.setdefault("MRA_EXEC", "local")
    clm = gate()

    matches = [i for i, (label, _) in enumerate(clm.SNIPPETS)
               if args.snippet.lower() in label.lower()]
    if not matches:
        print(f"No row matching {args.snippet!r}. Available:")
        for label, _ in clm.SNIPPETS:
            print(f"  {label}")
        return 2
    index = matches[0]
    label, body = clm.SNIPPETS[index]
    source = clm.MATHLIB + body

    from math_v2 import _local

    ok, why = _local.lean_available()
    if not ok:
        print(f"Lean is not available: {why}")
        return 2

    print(f"commit:  {commit()}")
    print(f"project: {_local.LEAN_PROJECT}")
    print(f"repl:    {os.getenv('MRA_LEAN_REPL_BIN') or 'lake exe repl'}")

    # ------------------------------------------------ identity of what we run
    from math_v2.tools import _repl, _util

    print("\nSAME IMPLEMENTATION?")
    print("-" * len("SAME IMPLEMENTATION?"))
    print(f"  case + compile path from  {clm.__file__}")
    print(f"  _util                     {_util.__file__}")
    print(f"  _repl                     {_repl.__file__}")
    print(f"  row                       {index + 1} of {len(clm.SNIPPETS)}  {label!r}")

    block("CASE SOURCE FROM compare_lean_modes.py:", source)

    routed = _repl.needs_subprocess(source)
    imports, rest = _repl.split_imports(source)
    print("\nROUTING DECISION:")
    print("-" * len("ROUTING DECISION:"))
    print(f"  leading imports    {imports}")
    print(f"  beyond the base    {_repl.extra_imports(imports)}")
    print(f"  needs_subprocess   {routed}")

    workdir = tempfile.mkdtemp(prefix="dbg_lean_")

    # Session state can only affect the REPL arm; `lake env lean` has no
    # history. So the subprocess arm always runs the row alone.
    prefix = clm.SNIPPETS[:index] if not args.isolated else []
    sequence = prefix + [clm.SNIPPETS[index]]

    print(f"\nrunning subprocess arm (1 row)...")
    sub_results, sub_trace = run_arm(clm, "subprocess", [clm.SNIPPETS[index]],
                                     os.path.join(workdir, "sub"))

    print(f"running REPL arm ({len(sequence)} row(s), one session)...")
    repl_results, repl_trace = run_arm(clm, "repl", sequence,
                                       os.path.join(workdir, "rpl"))

    sub_path, sub_source, _, sub_raw = sub_trace.last()
    repl_path, repl_source, _, repl_raw = repl_trace.last()

    print("\nBACKEND SELECTED:")
    print("-" * len("BACKEND SELECTED:"))
    print(f"  subprocess arm     {sub_path}")
    print(f"  repl arm           {repl_path}")

    block("SOURCE ACTUALLY PASSED TO COMPILER (subprocess arm):", sub_source)
    block("SOURCE ACTUALLY PASSED TO COMPILER (repl arm):", repl_source)
    if sub_source != repl_source:
        print("\n  *** THE TWO ARMS COMPILED DIFFERENT TEXT ***")

    block("RAW SUBPROCESS OUTPUT:", sub_raw)
    block("RAW REPL OUTPUT:", repl_raw)

    sub_outcome = sub_results[-1][1]
    repl_outcome = repl_results[-1][1]
    block("FINAL SUBPROCESS CLASSIFICATION:", sub_outcome.value)
    block("FINAL REPL CLASSIFICATION:", repl_outcome.value)

    if prefix:
        print("\nWHAT THE REPL ARM DID BEFORE THE TARGET ROW")
        print("-" * len("WHAT THE REPL ARM DID BEFORE THE TARGET ROW"))
        for (path, _, ok, _), (row_label, _) in zip(repl_trace.rows, sequence):
            print(f"  {row_label:<44} {path:<16} ok={ok}")

    print()
    if sub_outcome is repl_outcome:
        print(f"AGREE — both {sub_outcome.value}")
        if not prefix:
            print("\nRun without --isolated to reproduce the gate's ordering;")
            print("the row runs tenth there, not first.")
        return 0

    print(f"DISAGREE — subprocess {sub_outcome.value}, repl {repl_outcome.value}")
    if prefix:
        print("\nThe row above ran after the preceding rows, as in the gate.")
        print("If --isolated AGREES and this DISAGREES, the cause is sequence:")
        print("something earlier in the run changes how this row is served.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
