"""How much memory does one REPL session actually grow by, per command?

    python scripts/measure_repl_memory.py

WHY THIS EXISTS
---------------
`math_v2/tools/_repl.py` recycles the session after `MAX_COMMANDS` (default
200) commands, and says so honestly:

    "Deliberately NOT tuned. 200 is a conservative guess; the threshold is
    here to be measured later, not to be optimal now."

Every command derives a NEW environment from BASE, which the REPL retains —
there is no command to free one, so one session holding Mathlib only grows.
Guessing at the growth rate is exactly the mistake this project has already
paid for twice on the wall-clock side (`time_lean.py`'s own docstring): a
constant sized on different hardware, or on no hardware at all, either wastes
a session that could have run much longer, or a benchmark dies at goal 140
with an out-of-memory kill. This script replaces the guess with a number from
THIS machine.

WHY IT CANNOT BE MEASURED FROM THE WSL CLONE THIS WAS WRITTEN IN
------------------------------------------------------------------
The built `repl` binary and the Mathlib checkout it needs both live only on
the machine real evaluations run on (see any `eval/results/*.json`'s
`environment.repl_binary`). Run this there, not in a throwaway clone.

WHAT IT DOES
------------
Starts one real REPL session (the same `_repl.Session` the agent uses),
submits a cycle of small but varied theorem commands — varied because a
retained environment's size depends on what it proved, not just that
something was proved — and samples the process's resident memory every
`--sample-every` commands, well past the current MAX_COMMANDS so the trend
past today's recycle point is visible rather than assumed.

Needs `psutil` (not a hard dependency of the agent itself — see
requirements.txt) because Windows has no standard-library way to read a
process's working set. Install it with `pip install psutil` to run this.

DOES NOT NEED THE AGENT'S OWN DEPENDENCIES INSTALLED. MEASURED: a plain
`from math_v2.tools import _repl` drags in `math_v2/tools/__init__.py`,
which eagerly imports every tool including `control.py`, which needs
`langchain` — a real dependency of running the agent, and nothing to do
with reading one process's memory. `_repl.py` itself imports only `math_v2
._local` and the standard library, so it is loaded here by file path,
bypassing `math_v2.tools`'s own `__init__.py` entirely. This script should
run in an environment that has never installed the agent's requirements at
all, because it is answering a question about the Lean process, not the
agent wrapped around it.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psutil
except ImportError:
    print("This script needs `psutil` to read the REPL process's memory use.")
    print("It is not a dependency of the agent itself — install it just for")
    print("this measurement:\n\n    pip install psutil\n")
    raise SystemExit(2)


def _load_bare(name: str, path: Path):
    """Import one module by file path, without running its package's
    `__init__.py`. See the module docstring above for why this is needed."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


import math_v2  # noqa: E402 - safe: its own __init__ is lazy by design

_local = _load_bare("math_v2._local", ROOT / "math_v2" / "_local.py")
math_v2._local = _local
_repl = _load_bare("math_v2.tools._repl", ROOT / "math_v2" / "tools" / "_repl.py")

# Varied on purpose: a retained environment's size depends on what it proved.
# Cycling through these is closer to a real run than repeating one command,
# which would only measure the cost of the SIMPLEST possible retained proof.
COMMANDS = [
    "theorem mem_probe_a : True := trivial",
    "theorem mem_probe_b (n : Nat) : n + 0 = n := by simp",
    "theorem mem_probe_c (n : Nat) : ∃ p, n ≤ p ∧ p.Prime := Nat.exists_infinite_primes n",
    "theorem mem_probe_d (a b : Nat) : a + b = b + a := by ring",
    "theorem mem_probe_e : ¬ (True ∧ False) := by tauto",
    "theorem mem_probe_f (s : Set Nat) (h : s.Finite) : s.Finite := h",
]


def rss_mb(pid: int) -> float:
    """Resident memory of the WHOLE process tree rooted at `pid`, in MB.

    NOT just `Process(pid).memory_info().rss`. MEASURED: `argv()` launches
    `lake env <repl.exe>`, and on Windows `lake` does not replace its own
    process image the way a POSIX `exec` would — it spawns the real Lean
    process as a CHILD and waits on it, piping stdin/stdout through. A
    single-PID reading therefore reports the thin launcher (single-digit MB)
    and never the multi-GB process actually holding Mathlib, which is
    invisible under the parent's own number, not merely underestimated by
    it. Confirmed against a synthetic parent/child pair: the same reading
    that shows ~12 MB for the parent alone shows ~220 MB once a 200 MB
    child's memory is included.
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return 0.0
    total = proc.memory_info().rss
    for child in proc.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.NoSuchProcess:
            continue  # exited between listing and reading; not a real loss
    return total / (1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commands", type=int, default=400,
                        help="how many commands to run (default 400, past "
                             "today's MAX_COMMANDS=%d default)" % _repl.MAX_COMMANDS)
    parser.add_argument("--sample-every", type=int, default=10,
                        help="sample RSS every N commands (default 10)")
    args = parser.parse_args()
    total, every = args.commands, args.sample_every

    project = _local.LEAN_PROJECT
    if not project:
        print("MRA_LEAN_PROJECT is not set. Point it at the Lake project the")
        print("real agent uses — this has to be the same Mathlib checkout.")
        return 2

    print(f"project: {project}")
    print(f"repl binary: {_repl.argv()}")
    print(f"running {total} commands, sampling every {every}\n")

    session = _repl.Session(cwd=project).start()
    print(f"session started: {session._startup:.1f}s (Mathlib import, one-time)")
    baseline = rss_mb(session.process.pid)
    print(f"baseline RSS after start: {baseline:.0f} MB")
    # A sanity floor, not a hard failure: `_repl.py`'s own docstring puts one
    # Mathlib-loaded session at 4-6 GB. Anything far below that means `rss_mb`
    # is still not looking at the right process on THIS machine, and every
    # number below would be as meaningless as the single-PID reading this
    # replaced was.
    if baseline < 500:
        print(f"  WARNING: {baseline:.0f} MB is far below the 4-6 GB a loaded")
        print("  Mathlib session normally takes. The numbers below are likely")
        print("  measuring the wrong process on this machine — do not trust")
        print("  them without checking (e.g. Task Manager) what is actually")
        print("  using memory while this runs.")
    print()

    samples = [(0, baseline)]
    started = time.time()
    for i in range(1, total + 1):
        body = COMMANDS[i % len(COMMANDS)]
        try:
            session.command(body)
        except _repl.ReplUnavailable as exc:
            print(f"\nsession died at command {i}: {exc}")
            break
        if i % every == 0:
            mb = rss_mb(session.process.pid)
            samples.append((i, mb))
            grown = mb - baseline
            print(f"  {i:5d} commands   {mb:8.0f} MB   (+{grown:6.0f} MB since start)")

    session.close()

    if len(samples) < 3:
        print("\nToo few samples to fit a trend — increase --commands.")
        return 1

    # A simple slope over the back half: growth is not necessarily linear from
    # the very first few commands (allocator warm-up), and the back half is
    # what a long run actually experiences.
    mid = len(samples) // 2
    (n0, mb0), (n1, mb1) = samples[mid], samples[-1]
    per_command = (mb1 - mb0) / max(1, n1 - n0)

    elapsed = time.time() - started
    print(f"\n{elapsed:.0f}s total, {elapsed / total:.2f}s/command steady state")
    print(f"MEASURED growth (back half): {per_command:.2f} MB/command")
    print(f"Current MAX_COMMANDS: {_repl.MAX_COMMANDS} "
          f"-> ~{per_command * _repl.MAX_COMMANDS:.0f} MB retained at recycle")

    for headroom_gb in (2, 4, 8):
        if per_command <= 0:
            break
        budget_mb = headroom_gb * 1024
        suggested = int(budget_mb / per_command)
        print(f"  to cap retained memory at {headroom_gb} GB: "
              f"MRA_LEAN_REPL_MAX_COMMANDS={suggested}")

    print("\nThis reports; it does not change MAX_COMMANDS. Compare the")
    print("suggestion above against how much memory this machine can actually")
    print("give one long-running session, and set MRA_LEAN_REPL_MAX_COMMANDS")
    print("deliberately if the current default does not fit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
