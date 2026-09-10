"""One command that says whether an install works, and cannot lie about it.

WHY IT EXISTS. Five failed attempts at a single verification run, because the
setup this repo needs is spread across two docs files with two pieces in
neither:

    MRA_PROVER, MRA_EXEC                     docs/RUN_NEXT.md
    MRA_LEAN, MRA_LEAN_PROJECT               docs/HANDOFF.md
    MRA_LEAN_BACKEND, MRA_LEAN_REPL_BIN      nowhere

Throughout all five, `diagnose_lean.py` reported "Mathlib is reachable" --
truthfully, because its probe uses the subprocess path while every real run
uses the REPL.

These tests pin the rows that were actually wrong. A check whose FAIL
conditions are untested is a check that will pass on a broken install.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util


def _check():
    spec = importlib.util.spec_from_file_location(
        "check_install", ROOT / "scripts" / "check_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch, env, toolchain_ok=True, lean_on_path=True):
    """`--quick`, with the environment and the gates controlled."""
    module = _check()
    for name in ("MRA_PROVER", "MRA_EXEC", "MRA_LEAN_PROJECT",
                 "MRA_LEAN_BACKEND", "MRA_LEAN_REPL_BIN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    import config
    import verifiers.lean_runner as runner
    monkeypatch.setattr(config, "LEAN_PROJECT",
                        env.get("MRA_LEAN_PROJECT", ""))
    monkeypatch.setattr(runner, "lean_toolchain_works",
                        lambda *a, **k: toolchain_ok)
    monkeypatch.setattr(runner, "lean_is_available",
                        lambda *a, **k: lean_on_path)
    return module.main(["--quick"])


def _healthy(tmp_path):
    project = tmp_path / "workspace"
    (project / ".lake" / "packages").mkdir(parents=True)
    (project / "lakefile.toml").write_text("name = 'x'", encoding="utf-8")
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.33.0",
                                            encoding="utf-8")
    return project


# ------------------------------------------------- the state that cost 5 tries
def test_repl_backend_without_a_binary_is_fatal(tmp_path, monkeypatch, capsys):
    """MEASURED: this exact configuration returned `unavailable` on all 64
    checkable proofs while `diagnose_lean` reported Mathlib reachable and
    every other row passed."""
    code = _run(monkeypatch, {
        "MRA_PROVER": "math_v2", "MRA_EXEC": "local",
        "MRA_LEAN_PROJECT": str(_healthy(tmp_path)),
        "MRA_LEAN_BACKEND": "repl",
    })
    out = capsys.readouterr().out
    assert code == 1, out
    assert "MRA_LEAN_REPL_BIN" in out
    assert "unavailable" in out, "it must say what the symptom looks like"


def test_repl_backend_with_a_real_binary_passes(tmp_path, monkeypatch, capsys):
    binary = tmp_path / "repl.exe"
    binary.write_bytes(b"\x00")
    code = _run(monkeypatch, {
        "MRA_PROVER": "math_v2", "MRA_EXEC": "local",
        "MRA_LEAN_PROJECT": str(_healthy(tmp_path)),
        "MRA_LEAN_BACKEND": "repl", "MRA_LEAN_REPL_BIN": str(binary),
    })
    assert code == 0, capsys.readouterr().out


def test_a_missing_project_is_fatal(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, {"MRA_PROVER": "math_v2", "MRA_EXEC": "local"})
    out = capsys.readouterr().out
    assert code == 1
    assert "MRA_LEAN_PROJECT" in out


def test_a_broken_toolchain_is_fatal(tmp_path, monkeypatch, capsys):
    """`lean --version` exits 0 under an unconfigured elan, so an exit code
    is not evidence -- which is why the gate reads the banner."""
    code = _run(monkeypatch, {
        "MRA_PROVER": "math_v2", "MRA_EXEC": "local",
        "MRA_LEAN_PROJECT": str(_healthy(tmp_path)),
    }, toolchain_ok=False)
    out = capsys.readouterr().out
    assert code == 1
    assert "lean_toolchain_works" in out
    assert "elan default stable" in out, "a diagnosis must name the fix"


# ------------------------------------------------- warnings, not failures
def test_unset_exec_warns_and_names_the_symptom(tmp_path, monkeypatch, capsys):
    """A run WORKS with MRA_EXEC unset until a source needs a subprocess,
    and then routes to aura_framework. MEASURED: 1 of 68 claims, on a goal
    carrying `import Mathlib.Topology.Basic` in its statement."""
    code = _run(monkeypatch, {
        "MRA_PROVER": "math_v2",
        "MRA_LEAN_PROJECT": str(_healthy(tmp_path)),
    })
    out = capsys.readouterr().out
    assert code == 0, "unset MRA_EXEC is usable, so not fatal"
    assert "aura_framework" in out
    assert "MRA_EXEC=local" in out


def test_unset_prover_warns_that_it_is_the_baseline(tmp_path, monkeypatch,
                                                    capsys):
    """`MRA_PROVER` defaults to `pipeline`, the BASELINE -- so a bare run
    measures the wrong agent. It is recorded in the results file, so nothing
    is mislabelled, but nothing warned at the start either."""
    code = _run(monkeypatch, {
        "MRA_EXEC": "local",
        "MRA_LEAN_PROJECT": str(_healthy(tmp_path)),
    })
    out = capsys.readouterr().out
    assert code == 0
    assert "BASELINE" in out


def test_a_healthy_install_says_so_plainly(tmp_path, monkeypatch, capsys):
    code = _run(monkeypatch, {
        "MRA_PROVER": "math_v2", "MRA_EXEC": "local",
        "MRA_LEAN_PROJECT": str(_healthy(tmp_path)),
    })
    out = capsys.readouterr().out
    assert code == 0
    assert "this install works" in out or "usable, with" in out


def test_quick_says_it_did_not_compile_anything(tmp_path, monkeypatch, capsys):
    """`--quick` skips the only rows that prove Lean can build something, so
    it must not read as a clean bill of health."""
    _run(monkeypatch, {
        "MRA_PROVER": "math_v2", "MRA_EXEC": "local",
        "MRA_LEAN_PROJECT": str(_healthy(tmp_path)),
    })
    assert "no compile was attempted" in capsys.readouterr().out


def test_it_checks_both_compile_paths_not_just_one():
    """THE DEFECT IN `diagnose_lean` THAT THIS EXISTS TO AVOID: its probe
    used the subprocess path and reported success while the REPL -- which
    every real run uses -- could not start."""
    source = (ROOT / "scripts" / "check_install.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in source.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "subprocess" in body and "REPL" in body
    assert "lean_runner(" in body, "the REPL path must actually be exercised"
