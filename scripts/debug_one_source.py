"""One Lean source, both backends, everything printed. About a minute.

    python scripts/debug_one_source.py
    python scripts/debug_one_source.py --snippet "a stray import"
    python scripts/debug_one_source.py --file some.lean

WHY THIS EXISTS
---------------
The 16-snippet gate takes eight to ten minutes and, when one row disagrees,
tells you only that it disagreed. Iterating on a single deterministic input at
that price is the wrong loop. This runs ONE source and prints the original
text, the text actually sent to the REPL, the routing decision, and the full
untruncated output from both backends.

It also prints which git commit is checked out, because the first thing to
rule out when a fixed bug appears to persist is that the fix is not there.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The gate row that has been disagreeing. Exactly as `compare_lean_modes.py`
# builds it: the MATHLIB preamble plus the snippet body.
DEFAULT = (
    "import Mathlib\n"
    "import Mathlib.Topology.Order\n"
    "import Mathlib.Data.Real.Basic\n"
    "\n"
    "theorem cmp_j : True := trivial"
)


def commit():
    try:
        return subprocess.run(["git", "log", "--oneline", "-1"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return "(git unavailable)"


def source_for(args):
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if args.snippet:
        spec = importlib.util.spec_from_file_location(
            "clm", ROOT / "scripts" / "compare_lean_modes.py")
        clm = importlib.util.module_from_spec(spec)
        sys.modules["clm"] = clm
        spec.loader.exec_module(clm)
        for label, body in clm.SNIPPETS:
            if args.snippet.lower() in label.lower():
                print(f"snippet: {label}\n")
                return clm.MATHLIB + body
        print(f"No snippet matching {args.snippet!r}. Available:")
        for label, _ in clm.SNIPPETS:
            print(f"  {label}")
        raise SystemExit(2)
    return DEFAULT


def in_backend(backend, fn, *args):
    os.environ["MRA_LEAN_BACKEND"] = backend
    os.environ.pop("MRA_LEAN_REPL", None)
    from math_v2.tools import _repl, _util

    importlib.reload(_repl)
    importlib.reload(_util)
    _repl.shutdown()
    try:
        return fn(*args)
    finally:
        _repl.shutdown()


def compile_one(source, workdir):
    from math_v2.tools import _util

    _util.forget()
    os.makedirs(workdir, exist_ok=True)
    return asyncio.run(_util.lean_runner(workdir)(source))


def block(title, text):
    print(f"\n{title}")
    print("-" * len(title))
    print(text if text.strip() else "(empty)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snippet", help="match a row of compare_lean_modes.py")
    parser.add_argument("--file", help="a .lean file to use instead")
    args = parser.parse_args()

    os.environ.setdefault("MRA_EXEC", "local")
    source = source_for(args)

    from math_v2 import _local

    ok, why = _local.lean_available()
    if not ok:
        print(f"Lean is not available: {why}")
        return 2

    print(f"commit:  {commit()}")
    print(f"project: {_local.LEAN_PROJECT}")
    print(f"repl:    {os.getenv('MRA_LEAN_REPL_BIN') or 'lake exe repl'}")

    block("ORIGINAL SOURCE:", source)

    # --- what the REPL path would do with it, decided offline
    from math_v2.tools import _repl

    routed = _repl.needs_subprocess(source)
    imports, body = _repl.split_imports(source)

    print("\nROUTING DECISION")
    print("-" * len("ROUTING DECISION"))
    print(f"  leading imports      {imports}")
    print(f"  beyond the base      {_repl.extra_imports(imports)}")
    print(f"  needs_subprocess     {routed}")
    if routed:
        print("\n  This source does NOT go to the session. In REPL mode it is")
        print("  compiled by `lake env lean`, the same function the subprocess")
        print("  arm calls, on the same text. The two arms therefore CANNOT")
        print("  disagree on it. If they do, the two arms are not running the")
        print("  same code — check the commit above on both machines.")

    block("SOURCE SENT TO REPL:",
          "(nothing — routed to `lake env lean`)" if routed else body)

    workdir = tempfile.mkdtemp(prefix="dbg_lean_")

    print("\nrunning subprocess arm...")
    sub = in_backend("subprocess", compile_one, source,
                     os.path.join(workdir, "sub"))
    print("running REPL arm...")
    rpl = in_backend("repl", compile_one, source, os.path.join(workdir, "rpl"))

    block("SUBPROCESS RESULT:", f"{sub.outcome.value}\n\n{sub.output}")
    block("REPL RESULT:", f"{rpl.outcome.value}\n\n{rpl.output}")

    print()
    if sub.outcome is rpl.outcome:
        print(f"AGREE — both {sub.outcome.value}")
        return 0

    print(f"DISAGREE — subprocess {sub.outcome.value}, repl {rpl.outcome.value}")
    if routed:
        print()
        print("...which should be impossible on this commit, because both arms")
        print("call `_subprocess_compile` for this source. The likeliest cause")
        print("is that this machine is running an older commit than the one")
        print("printed at the top. `git log --oneline -1` on both, and compare.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
