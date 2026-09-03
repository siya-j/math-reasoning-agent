"""The model is told how much room it has, and how much of it is left.

WHY THIS EXISTS
---------------
MEASURED, on the PutnamBench run in `eval/results/putnam-run2.json`:

  * `putnam_1962_a6` spent attempts 19-38 -- TWENTY consecutive skeletons,
    half of a 40-compile budget -- writing new decompositions instead of
    closing the holes it already had.
  * `putnam_1962_a4` spent attempts 17-34 bouncing off the same error, ten of
    its thirty-five attempts byte-identical to an earlier one.

Two things were true of that run at once. The prompt hard-coded "roughly ten
tool calls that matter, not the twenty the step limit allows" -- numbers
written against the DEFAULTS (12 compiles, 40 steps) and left untouched by
`--budget-profile hard-reasoning`, which had actually granted 40 and 120. And
`budget.summary` reported only what had been SPENT, never what remained; the
sole "compilation(s) left" figure the model could see came from a SEARCH
redirect in `budget.spend`, and that stretch of the run barely searched.

So the agent was paying for forty compiles while being told it had ten, and
was given no running figure to pace against either way. These tests hold both
halves closed: the prompt renders the real limits, and every tool that spends
a compilation reports what is left when it returns.
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from math_v2.context import MathContext
from math_v2.core import budget
from math_v2.prompt import MATH_SYSTEM_PROMPT, system_prompt
from math_v2.tools import proving as proving_tools
from math_v2.tools.proving import try_lemma, try_proof

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def runtime_for(workdir):
    from langchain.tools import ToolRuntime

    return ToolRuntime(state=None, context=MathContext(workdir=str(workdir)),
                       config={}, stream_writer=lambda *a, **k: None,
                       tool_call_id="t", store=None)


@pytest.fixture
def lean_calls(monkeypatch):
    calls = []

    async def fake(source):
        calls.append(source)
        from verifiers.lean_runner import LeanOutcome, LeanResult

        return LeanResult(LeanOutcome.COMPILED, "")

    monkeypatch.setattr(proving_tools, "lean_runner", lambda workdir: fake)
    return calls


# ------------------------------------------------- the prompt's own numbers
def test_the_rendered_prompt_has_no_leftover_placeholders():
    """`MATH_SYSTEM_PROMPT` is a template now. A caller that concatenated it
    raw would ship literal `{max_lean}` to the model."""
    rendered = system_prompt()

    assert "{" not in rendered and "}" not in rendered, rendered[:200]


def test_the_rendered_prompt_states_the_real_limits():
    rendered = system_prompt()

    assert f"{budget.MAX_LEAN_CALLS} compilations" in rendered
    assert f"{budget.MAX_TOOL_CALLS} tool calls" in rendered
    assert f"{budget.MAX_SEARCHES} searches" in rendered


def test_the_stale_hard_coded_budget_sentence_is_gone():
    """THE measured bug, stated as an assertion. This exact sentence told a
    40-compile run that it had ten calls that mattered."""
    assert "roughly ten tool calls" not in MATH_SYSTEM_PROMPT
    assert "the twenty the step limit allows" not in MATH_SYSTEM_PROMPT


def test_the_prompt_tracks_a_changed_budget():
    """THE regression test, and it must be a subprocess: `budget`'s constants
    are read from the environment ONCE at its first import, so only a fresh
    interpreter can observe the prompt rendering against a different profile
    -- the same reason `test_budget_profile_flag.py` reaches for one."""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from math_v2.prompt import system_prompt; "
        "t = system_prompt(); "
        "assert '40 compilations' in t, t[:80]; "
        "assert '120 tool calls' in t, t[:80]; "
        "assert '12 compilations' not in t; "
        "print('OK')"
    ) % str(ROOT)
    env = {
        "MRA_MAX_AGENT_LEAN": "40", "MRA_MAX_AGENT_STEPS": "120",
        "MRA_MAX_AGENT_SEARCHES": "40", "MRA_MAX_AGENT_SECONDS": "3600",
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run([sys.executable, "-c", code],
                            capture_output=True, text=True, env=env)

    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------- headroom
def test_headroom_reports_what_is_left_not_what_is_spent(tmp_path):
    """The complement of `summary`, which reports the spent side and is read
    by `finish` -- after every decision has already been made."""
    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.spend(workdir, lean=True)

    left = budget.headroom(workdir)
    spent = budget.summary(workdir)

    assert spent["lean_calls"] == 1
    assert left["lean_calls_left"] == budget.MAX_LEAN_CALLS - 1
    assert left["tool_calls_left"] == budget.MAX_TOOL_CALLS - 1


def test_headroom_never_reports_a_negative_allowance(tmp_path, monkeypatch):
    """`try_skeleton` may spend several compiles inside one tool call, so the
    spent count can legitimately pass the cap. "-3 compilations left" would be
    a worse signal than none at all."""
    workdir = str(tmp_path)
    budget.reset(workdir)
    monkeypatch.setattr(budget, "MAX_LEAN_CALLS", 2)
    budget.charge_lean(workdir, 9)

    assert budget.headroom(workdir)["lean_calls_left"] == 0
    assert "0 of 2 compilations" in budget.headroom_line(workdir)


def test_the_headroom_line_names_both_the_remainder_and_the_limit(tmp_path):
    """"27 left" alone cannot be paced against without knowing 27 of what."""
    workdir = str(tmp_path)
    budget.reset(workdir)

    line = budget.headroom_line(workdir)

    assert f"of {budget.MAX_LEAN_CALLS} compilations" in line
    assert f"of {budget.MAX_TOOL_CALLS} tool calls" in line


# ------------------------------------------------- through the real tools
def test_a_compiling_tool_reports_what_is_left(tmp_path, lean_calls):
    """Tested THROUGH the tool, not against the helper: the measured failure
    was that this figure never reached the model, and a test of `headroom`
    alone would repeat exactly that mistake."""
    workdir = str(tmp_path)
    budget.reset(workdir)
    rt = runtime_for(tmp_path)

    result = run(try_lemma.ainvoke({
        "statement": "lemma helper (n : Nat) : n + 0 = n",
        "proof": "by simp", "runtime": rt}))

    assert "BUDGET LEFT:" in result["message"], result["message"]
    assert result["outputs"]["budget_left"]["lean_calls_left"] == (
        budget.MAX_LEAN_CALLS - 1)


def test_the_reported_remainder_falls_as_the_budget_is_spent(
        tmp_path, lean_calls):
    workdir = str(tmp_path)
    budget.reset(workdir)
    rt = runtime_for(tmp_path)

    seen = []
    for _ in range(3):
        result = run(try_proof.ainvoke({"proof": "trivial", "runtime": rt,
                                        "statement": "theorem t : 2 + 2 = 4"}))
        seen.append(result["outputs"]["budget_left"]["lean_calls_left"])

    assert seen == sorted(seen, reverse=True), seen
    assert len(set(seen)) == 3, seen


def test_a_budget_stop_is_not_annotated(tmp_path, monkeypatch, lean_calls):
    """A stop already says what the budget did, in its own more specific
    words. Appending "0 of 0 compilations left" would only bury it."""
    monkeypatch.setattr(budget, "MAX_LEAN_CALLS", 0)
    budget.reset(str(tmp_path))
    rt = runtime_for(tmp_path)

    result = run(try_proof.ainvoke({"proof": "trivial", "runtime": rt,
                                    "statement": "theorem t : 2 + 2 = 4"}))

    assert "BUDGET LEFT:" not in (result.get("message") or "")
