"""Run every experiment and file the results in one folder.

    python scripts/run_experiments.py
    python scripts/run_experiments.py --only baseline-langchain
    python scripts/run_experiments.py --skip proofs-near

Creates eval/results/<timestamp>/ containing, for every step:

    <step>.log        everything it printed
    <step>.json       its machine-readable result, where it produces one
    manifest.json     what ran, exit codes, durations, and the environment

Designed to be run unattended on a machine with no other tooling. A failing
step is recorded and the run continues — one broken experiment must not cost
you the other five.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"


# name, argv, extra environment, artefact produced by the step
STEPS: list[tuple[str, list[str], dict, str | None]] = [
    (
        "tests",
        [sys.executable, "-m", "pytest", "-q"],
        {},
        None,
    ),
    (
        "check-model",
        [sys.executable, "scripts/check_model.py"],
        {},
        None,
    ),
    (
        "baseline-langchain",
        [sys.executable, "scripts/evaluate.py"],
        {"MRA_HARNESS": "langchain"},
        "eval/last_run.json",
    ),
    (
        "baseline-deepagents",
        [sys.executable, "scripts/evaluate.py"],
        {"MRA_HARNESS": "deepagents"},
        "eval/last_run.json",
    ),
    (
        "probe-lean",
        [sys.executable, "scripts/probe_lean_model.py"],
        {},
        None,
    ),
    (
        "probe-lean-nosearch",
        [sys.executable, "scripts/probe_lean_model.py", "--no-search"],
        {},
        None,
    ),
    (
        "proofs-in-mathlib",
        [
            sys.executable,
            "scripts/evaluate_proofs.py",
            "--tier",
            "in-mathlib",
            "--depth",
            "0",
            "--review",
        ],
        {},
        "eval/last_proof_run.json",
    ),
    (
        "proofs-near-mathlib",
        [
            sys.executable,
            "scripts/evaluate_proofs.py",
            "--tier",
            "near-mathlib",
            "--depth",
            "0",
            "--review",
        ],
        {},
        "eval/last_proof_run.json",
    ),
]


def environment() -> dict:
    """Everything needed to interpret the results later."""
    def version(package: str) -> str:
        try:
            from importlib.metadata import version as v

            return v(package)
        except Exception:
            return "not installed"

    return {
        "when": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "MRA_MODEL": os.getenv("MRA_MODEL", "(unset — config default)"),
        "MRA_LEAN_PROJECT": os.getenv("MRA_LEAN_PROJECT", "(unset)"),
        "MRA_LEAN_TIMEOUT": os.getenv("MRA_LEAN_TIMEOUT", "(default)"),
        "lean_found": shutil.which("lean") is not None,
        "lake_found": shutil.which("lake") is not None,
        "packages": {
            name: version(name)
            for name in ("langchain", "deepagents", "sympy", "langchain-google-genai")
        },
        "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
    }


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return "unknown"


def run_step(name, argv, extra_env, artefact, folder) -> dict:
    print(f"\n{'=' * 66}\n  {name}\n{'=' * 66}", flush=True)

    env = {**os.environ, **extra_env}
    started = time.monotonic()

    try:
        completed = subprocess.run(
            argv, cwd=ROOT, env=env, capture_output=True, text=True
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        code = completed.returncode
    except Exception as exc:  # a broken step must not end the run
        output = f"FAILED TO START: {exc}"
        code = -1

    elapsed = round(time.monotonic() - started, 1)
    print(output[-4000:] if len(output) > 4000 else output, flush=True)
    print(f"  -> exit {code} in {elapsed}s", flush=True)

    (folder / f"{name}.log").write_text(output, encoding="utf-8")

    saved = None
    if artefact:
        source = ROOT / artefact
        if source.exists():
            saved = f"{name}.json"
            shutil.copy(source, folder / saved)

    return {
        "step": name,
        "command": " ".join(argv[1:]),
        "env": extra_env,
        "exit_code": code,
        "seconds": elapsed,
        "log": f"{name}.log",
        "result": saved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", help="run only these steps")
    parser.add_argument("--skip", action="append", default=[], help="skip these steps")
    parser.add_argument("--list", action="store_true", help="show step names and exit")
    args = parser.parse_args()

    if args.list:
        for name, argv, env, _ in STEPS:
            flags = " ".join(f"{k}={v}" for k, v in env.items())
            print(f"  {name:<22} {flags} {' '.join(argv[1:])}")
        return 0

    steps = [s for s in STEPS if s[0] not in args.skip]
    if args.only:
        steps = [s for s in steps if s[0] in args.only]

    folder = RESULTS / datetime.now().strftime("%Y-%m-%d_%H%M")
    folder.mkdir(parents=True, exist_ok=True)

    env = environment()
    print(f"Results -> {folder}")
    print(f"Model:     {env['MRA_MODEL']}")
    print(f"Lean:      {'found' if env['lean_found'] else 'NOT FOUND'}")
    print(f"Commit:    {env['git_commit']}{' (dirty)' if env['git_dirty'] else ''}")
    print(f"Steps:     {len(steps)}")

    records = [run_step(*step, folder) for step in steps]

    manifest = {"environment": env, "steps": records}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\n{'=' * 66}\n  SUMMARY\n{'=' * 66}")
    for record in records:
        mark = "ok  " if record["exit_code"] == 0 else f"exit {record['exit_code']}"
        print(f"  {mark:<8} {record['step']:<22} {record['seconds']:>7}s")
    print(f"\nEverything saved to: {folder}")
    print("Send that whole folder for analysis — it is self-contained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
