"""Phase 1: MRA_MAX_AGENT_SECONDS is a real wall clock. Plus Option A. Offline.

THE FAILURE, TRACED
-------------------
`budget.spend()` reads the clock, and it is only ever called from inside a
tool. Time that passes when no tool is running was therefore invisible to the
budget until the next tool call, by which point it had already been spent.

What runs between tool calls is the model. On exercise_1_19b:

    statement check: does NOT elaborate      compile 1
    statement check: does NOT elaborate      compile 2
    statement check: does NOT elaborate      compile 3
    budget: time budget spent (1031s of 300s)
    agent failed:

Three compiles put the third one ending near t=330. The next budget
observation was at t=1031 — roughly 700 seconds with NO tool running, inside
one model call that was retrying with backoff. The run reported "0 model"
because `_count_model_calls` reads the result, which the exception path never
produced.

`test_time_spent_between_tool_calls_is_now_bounded` reproduces exactly that
shape: an agent that calls one tool and then sleeps.
"""

import asyncio
import time

import pytest

from eval.proof_metrics import ProofOutcome, classify
from math_v2 import harness
from math_v2.core import budget, log
from math_v2.tools import _util
from verifiers.lean_runner import LeanOutcome, LeanResult


def build_stalling_agent(stall_seconds, before=None):
    """An agent that calls a tool, then stalls — the measured failure shape."""

    class Agent:
        async def ainvoke(self, payload, context=None):
            if before:
                before(context.workdir)
            await asyncio.sleep(stall_seconds)
            return {"messages": [{"role": "assistant", "content": "done"}]}

    def factory(model, tools, system_prompt):
        return Agent()

    return factory


def build_quick_agent(text="nothing found"):
    class Agent:
        async def ainvoke(self, payload, context=None):
            return {"messages": [{"role": "assistant", "content": text}]}

    return lambda model, tools, prompt: Agent()


# ------------------------------------------- 1. the wall clock is now real
def test_time_spent_between_tool_calls_is_now_bounded(tmp_path, monkeypatch):
    """THE REGRESSION. Before this, an agent that stalled outside a tool ran
    for as long as it liked — measured at 1032s against a 300s budget."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 0.3)
    monkeypatch.setattr(budget, "WALL_CLOCK_MARGIN", 0.2)

    started = time.monotonic()
    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=build_stalling_agent(30))
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"the stall was not interrupted ({elapsed:.1f}s)"
    assert run is not None


def test_a_stalled_run_is_classified_exhausted_not_error(tmp_path, monkeypatch):
    """Requirement: a timeout produces the appropriate stopped outcome rather
    than an exception. EXHAUSTED already means "ran out of clock"; a wall-clock
    stop is the same fact read a different way, so no new outcome was added.

    The agent formalises FIRST and then stalls, because that is the case the
    wall clock is about. A run that stalls before producing any statement is
    NOT_FORMALIZED and rightly so — classify checks that first, and it is the
    more specific fact. That is what exercise_1_19b actually was.
    """
    monkeypatch.setattr(budget, "MAX_SECONDS", 0.3)
    monkeypatch.setattr(budget, "WALL_CLOCK_MARGIN", 0.2)

    def formalised(workdir):
        log.set_goal(workdir, "theorem mra_goal : True")
        log.append(workdir, log.Record(kind=log.STATEMENT_CHECK,
                                       statement="theorem mra_goal : True",
                                       status=log.TRUE))

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=build_stalling_agent(30, before=formalised))

    assert classify(run) is ProofOutcome.EXHAUSTED


def test_a_run_that_stalls_before_formalising_is_not_formalized(tmp_path, monkeypatch):
    """The other side of the same rule, pinned so the ordering cannot drift.
    exercise_1_19b stalled AND never elaborated; the statement is the more
    specific fact and must win."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 0.3)
    monkeypatch.setattr(budget, "WALL_CLOCK_MARGIN", 0.2)

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=build_stalling_agent(30))

    assert classify(run) is ProofOutcome.NOT_FORMALIZED
    assert budget.summary(str(tmp_path))["terminated_early"] is True


def test_the_stop_is_recorded_where_the_evaluator_reads_it(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "MAX_SECONDS", 0.3)
    monkeypatch.setattr(budget, "WALL_CLOCK_MARGIN", 0.2)

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=build_stalling_agent(30))

    assert any(e.startswith("stopped early") for e in run.trace), run.trace
    assert budget.summary(str(tmp_path))["terminated_early"] is True


def test_work_done_before_the_stall_is_not_lost(tmp_path, monkeypatch):
    """A timeout must cost the prose and nothing else. Everything the agent
    actually did is already on disk."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 0.3)
    monkeypatch.setattr(budget, "WALL_CLOCK_MARGIN", 0.2)

    def did_work(workdir):
        log.append(workdir, log.Record(kind=log.PROOF, statement="theorem t : True",
                                       proof="by trivial", status=log.UNKNOWN))

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=build_stalling_agent(30, before=did_work))

    assert len(run.attempts) == 1, "the recorded attempt was lost"


def test_a_run_that_finishes_in_time_is_untouched(tmp_path, monkeypatch):
    """The deadline must be invisible to every healthy run — the seven
    near-Mathlib goals must behave exactly as they did."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 60.0)

    run = harness.prove("q", model=object(), workdir=str(tmp_path),
                        agent_factory=build_quick_agent())

    assert not budget.summary(str(tmp_path))["terminated_early"]
    assert not any(e.startswith("stopped early") for e in run.trace)


def test_the_deadline_is_later_than_the_inner_budget(monkeypatch):
    """Otherwise the outer clock would cut off a compile the inner budget had
    just legitimately authorised, turning a working run into a timeout."""
    monkeypatch.setattr(budget, "MAX_SECONDS", 300.0)
    monkeypatch.setattr(budget, "WALL_CLOCK_MARGIN", None)

    assert budget.wall_clock_deadline() > budget.MAX_SECONDS


def test_the_margin_covers_one_in_flight_compile(monkeypatch):
    """asyncio.to_thread cannot be cancelled, so a compile already running is
    bounded by subprocess timeout, not by us. The margin is sized to it so the
    worst case is finite and known rather than unbounded."""
    from math_v2 import _aura

    monkeypatch.setattr(budget, "MAX_SECONDS", 300.0)
    monkeypatch.setattr(budget, "WALL_CLOCK_MARGIN", None)

    assert budget.wall_clock_deadline() == 300.0 + _aura.DEFAULT_TIMEOUT


def test_the_deadline_is_configurable(monkeypatch):
    monkeypatch.setattr(budget, "MAX_SECONDS", 100.0)
    monkeypatch.setattr(budget, "WALL_CLOCK_MARGIN", 5.0)

    assert budget.wall_clock_deadline() == 105.0


def test_terminate_is_indistinguishable_from_an_in_tool_stop(tmp_path):
    """Downstream code must not need to know which clock fired."""
    workdir = str(tmp_path)
    budget.reset(workdir)

    budget.terminate(workdir, "wall clock spent (480s)")

    spent = budget.summary(workdir)
    assert spent["terminated_early"] is True
    assert "wall clock" in spent["reason"]
    # And every subsequent tool refuses, exactly as after a budget stop.
    assert budget.spend(workdir, lean=True)["terminated"] is True


def test_terminate_never_raises():
    budget.terminate("/nonexistent/path/that/cannot/be/written", "x")


# ------------------------------- 2. Option A: statement checks are capped
def test_a_third_statement_check_is_refused(tmp_path, monkeypatch):
    """exercise_1_13c: three checks, ~135s, 45% of a 300s budget, before the
    agent attempted any mathematics."""
    monkeypatch.setattr(budget, "MAX_STATEMENT_CHECKS", 2)
    workdir = str(tmp_path)
    budget.reset(workdir)

    assert budget.spend(workdir, lean=True, statement_check=True) is None
    assert budget.spend(workdir, lean=True, statement_check=True) is None
    stop = budget.spend(workdir, lean=True, statement_check=True)

    assert stop is not None
    assert stop["error"] == budget.REDIRECT
    assert stop["terminated"] is False, "the run is not over, only re-checking"
    assert "not_formalized" in stop["message"]


def test_the_refused_check_is_still_charged(tmp_path, monkeypatch):
    """An agent that only ever re-checks must still be bounded by tool_calls."""
    monkeypatch.setattr(budget, "MAX_STATEMENT_CHECKS", 1)
    workdir = str(tmp_path)
    budget.reset(workdir)

    budget.spend(workdir, lean=True, statement_check=True)
    before = budget.read(workdir)["tool_calls"]
    budget.spend(workdir, lean=True, statement_check=True)

    assert budget.read(workdir)["tool_calls"] == before + 1


def test_capping_checks_does_not_cap_proving(tmp_path, monkeypatch):
    """The compile budget is separate. Spending the check allowance must leave
    every proof attempt available — that is the entire point."""
    monkeypatch.setattr(budget, "MAX_STATEMENT_CHECKS", 1)
    workdir = str(tmp_path)
    budget.reset(workdir)

    budget.spend(workdir, lean=True, statement_check=True)
    budget.spend(workdir, lean=True, statement_check=True)   # refused

    assert budget.spend(workdir, lean=True, goal_state=True) is None


def test_only_check_statement_is_charged_as_a_check():
    import inspect

    from math_v2.tools import proving as proving_tools

    src = inspect.getsource(proving_tools)
    assert src.count("statement_check=True") == 1


def test_the_count_is_reported(tmp_path):
    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.spend(workdir, lean=True, statement_check=True)

    assert budget.summary(workdir)["statement_checks"] == 1


def test_refund_statement_check_undoes_one_charge(tmp_path):
    """MEASURED: a cold-start `lake env lean` timeout, or a crashed REPL
    session, used to burn one of only MAX_STATEMENT_CHECKS=2 formalisation
    attempts exactly like a genuine syntax mistake would -- half the budget
    spent on a question the compiler never actually judged."""
    workdir = str(tmp_path)
    budget.reset(workdir)

    budget.spend(workdir, lean=True, statement_check=True)
    budget.refund_statement_check(workdir)

    assert budget.summary(workdir)["statement_checks"] == 0


def test_refund_never_goes_negative(tmp_path):
    workdir = str(tmp_path)
    budget.reset(workdir)

    budget.refund_statement_check(workdir)

    assert budget.summary(workdir)["statement_checks"] == 0


def test_a_refunded_check_leaves_room_for_a_genuine_third_attempt(tmp_path, monkeypatch):
    """The actual point: refunding an infra failure must leave the cap able
    to accept another real check, not just report a nicer number afterward."""
    monkeypatch.setattr(budget, "MAX_STATEMENT_CHECKS", 2)
    workdir = str(tmp_path)
    budget.reset(workdir)

    assert budget.spend(workdir, lean=True, statement_check=True) is None
    budget.refund_statement_check(workdir)  # that one was an infra failure
    assert budget.spend(workdir, lean=True, statement_check=True) is None
    assert budget.spend(workdir, lean=True, statement_check=True) is None
    # Two GENUINE checks are now charged (the refunded one doesn't count), so
    # a third is refused exactly as it would be without ever hitting an
    # infra failure -- the cap still means what it always meant.
    stop = budget.spend(workdir, lean=True, statement_check=True)
    assert stop is not None


# --------------------------- 3. Option A: identical source is not recompiled
class CountingBackend:
    def __init__(self, outcome=LeanOutcome.ERRORS):
        self.calls = 0
        self.outcome = outcome

    async def run(self, argv, workdir, stdin=None, timeout=None, cwd=None):
        self.calls += 1

        class Result:
            ok = self.outcome is LeanOutcome.COMPILED
            returncode = 0
            stdout = "" if ok else "error: unknown identifier"
            stderr = ""
            stdout_path = ""
            stderr_path = ""
            outputs = {}

        return Result()


@pytest.fixture
def local_backend(monkeypatch):
    from math_v2 import _local

    monkeypatch.setattr(_local, "MODE", "local")
    monkeypatch.setattr(_local, "enabled", lambda: True)
    _util.forget()
    yield _local
    _util.forget()


def test_identical_source_is_compiled_once(tmp_path, local_backend, monkeypatch):
    """~45s saved per repeat, of which ~35s is re-importing Mathlib."""
    backend = CountingBackend()
    monkeypatch.setattr(local_backend, "run", backend.run)
    run_lean = _util.lean_runner(str(tmp_path))
    source = "import Mathlib\ntheorem t : True := trivial"

    first = asyncio.run(run_lean(source))
    second = asyncio.run(run_lean(source))

    assert backend.calls == 1, "the same source was compiled twice"
    assert first.outcome is second.outcome


def test_different_source_is_always_compiled(tmp_path, local_backend, monkeypatch):
    backend = CountingBackend()
    monkeypatch.setattr(local_backend, "run", backend.run)
    run_lean = _util.lean_runner(str(tmp_path))

    asyncio.run(run_lean("import Mathlib\ntheorem a : True := trivial"))
    asyncio.run(run_lean("import Mathlib\ntheorem b : True := trivial"))

    assert backend.calls == 2


def test_a_transient_failure_is_never_cached(tmp_path, local_backend, monkeypatch):
    """A timeout or a missing compiler says nothing about the source. Caching
    it would turn one bad moment into a permanent failure for the whole goal."""
    async def explode(argv, workdir, stdin=None, timeout=None, cwd=None):
        raise RuntimeError("lake vanished")

    monkeypatch.setattr(local_backend, "run", explode)
    run_lean = _util.lean_runner(str(tmp_path))

    first = asyncio.run(run_lean("import Mathlib\ntheorem t : True := trivial"))
    assert first.outcome is LeanOutcome.UNAVAILABLE

    backend = CountingBackend(LeanOutcome.COMPILED)
    monkeypatch.setattr(local_backend, "run", backend.run)
    second = asyncio.run(run_lean("import Mathlib\ntheorem t : True := trivial"))

    assert backend.calls == 1, "a transient failure was cached"
    assert second.outcome is LeanOutcome.COMPILED


def test_the_cache_does_not_cross_goals(tmp_path, local_backend, monkeypatch):
    """Two goals in one benchmark process must not share compiles."""
    backend = CountingBackend()
    monkeypatch.setattr(local_backend, "run", backend.run)
    source = "import Mathlib\ntheorem t : True := trivial"

    asyncio.run(_util.lean_runner(str(tmp_path / "a"))(source))
    asyncio.run(_util.lean_runner(str(tmp_path / "b"))(source))

    assert backend.calls == 2


def test_a_reused_workdir_is_cleared_by_prove(tmp_path, monkeypatch):
    """`harness.prove` calls forget(). Otherwise a second goal in the same
    directory would inherit the first one's verdicts."""
    _util._memo[(str(tmp_path), "deadbeef")] = LeanResult(LeanOutcome.COMPILED)
    monkeypatch.setattr(budget, "MAX_SECONDS", 60.0)

    harness.prove("q", model=object(), workdir=str(tmp_path),
                  agent_factory=build_quick_agent())

    assert not [k for k in _util._memo if k[0] == str(tmp_path)]


def test_the_anticheat_still_runs_on_a_cached_result(tmp_path, local_backend,
                                                     monkeypatch):
    """The cache stores the CLASSIFIED result, so `sorry` stays INCOMPLETE.
    A cache that stored raw output and skipped `_classify` would be a way to
    launder a cheat through a repeat compile."""
    backend = CountingBackend(LeanOutcome.COMPILED)
    monkeypatch.setattr(local_backend, "run", backend.run)
    run_lean = _util.lean_runner(str(tmp_path))
    source = "import Mathlib\ntheorem t : True := by sorry"

    first = asyncio.run(run_lean(source))
    second = asyncio.run(run_lean(source))

    assert first.outcome is LeanOutcome.INCOMPLETE
    assert second.outcome is LeanOutcome.INCOMPLETE
    assert backend.calls == 1


# ------------- a budget stop is EXHAUSTED even when the agent obeys it
def test_hitting_the_compile_budget_is_recorded_as_stopped_early(tmp_path):
    """MEASURED on proofnet `exercise_1_2`. The agent proved both helper
    lemmas, hit the compile limit, was told to stop, and stopped — so
    `terminated` was never set, no note was written, and the run was scored
    NOT_PROVED instead of EXHAUSTED.

    The incentive that created is backwards: ignoring the stop and burning the
    grace got you excluded from the denominator; obeying it got you counted as
    a proving failure.
    """
    from domain.proof import ProofRun
    from eval.proof_metrics import ProofOutcome, classify
    from math_v2.core import budget

    workdir = str(tmp_path)
    budget.reset(workdir)
    monkey = budget.MAX_LEAN_CALLS
    try:
        budget.MAX_LEAN_CALLS = 1
        assert budget.spend(workdir, lean=True) is None      # the one allowed
        stop = budget.spend(workdir, lean=True)              # blocked
    finally:
        budget.MAX_LEAN_CALLS = monkey

    spent = budget.summary(workdir)

    assert stop is not None, "the second compile was not refused"
    assert spent["terminated_early"] is False, "grace was not consumed"
    assert spent["reason"], "no reason recorded for the block"

    # What `harness` now writes, and what `classify` reads.
    run_ = ProofRun(goal="q", statement="theorem t : True", statement_ok=True)
    run_.trace.append(f"stopped early: {spent['reason']}")

    assert classify(run_) is ProofOutcome.EXHAUSTED


def test_a_search_redirect_is_not_mistaken_for_exhaustion(tmp_path):
    """`_over` sets `reason` only for the time, tool and compile budgets. A
    search redirect must not make a goal look exhausted."""
    from math_v2.core import budget

    workdir = str(tmp_path)
    budget.reset(workdir)
    for _ in range(budget.MAX_CONSECUTIVE_SEARCHES + 2):
        budget.spend(workdir, search=True)

    assert budget.summary(workdir)["reason"] == "", (
        "a search redirect set the exhaustion reason"
    )
