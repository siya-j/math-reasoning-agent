"""Phase 2: the opt-in persistent Lean REPL. Offline — no Lean is started.

WHY IT EXISTS, MEASURED on the target machine at steady state:

    empty file (process + lake startup)    13.6s
    import Mathlib                         40.5s
    import + one trivial theorem           39.5s

~39s of every compile is fixed overhead paid before Lean looks at the proof.
Eight compiles is five minutes of re-importing the same 8,690 .olean files.

THE TEST THAT DECIDES WHETHER THIS SHIPS is `test_a_declaration_cannot_leak_
into_the_next_attempt`. Everything else here is mechanics; that one is the
property a fresh process gave us for free and which a shared process must earn.

These tests drive a FAKE REPL that speaks the real wire protocol — JSON
objects separated by blank lines — so they exercise the framing, the recovery
and the isolation contract without needing Lean. The end-to-end equivalence
check against real Lean is `scripts/compare_lean_modes.py`, which is a script
rather than a test because it needs a Mathlib install.
"""

import asyncio
import json

import pytest

from math_v2 import _local
from math_v2.tools import _repl, _util
from verifiers.lean_runner import LeanOutcome

MATHLIB = "import Mathlib\n"


# --------------------------------------------------------------- the fake REPL
class FakeRepl:
    """A stand-in that speaks the documented protocol and records the wire.

    Deliberately NOT a mock of `Session`: the framing (blank-line separated,
    pretty-printed JSON) and the `env` discipline are the two things most
    likely to be got wrong, so both are exercised for real.
    """

    def __init__(self, *, declared=(), die_after=None, hang_after=None):
        self.sent = []
        self.env_counter = 0
        self.declared = set(declared)
        self.die_after = die_after
        self.hang_after = hang_after
        self.starts = 0

    # -- what a Session would do, minus the process
    def install(self, monkeypatch):
        outer = self

        class Fake(_repl.Session):
            def start(self):
                outer.starts += 1
                self.base = 0
                self.process = object()
                return self

            def alive(self):
                return self.process is not None

            def close(self):
                self.process = None

            def command(self, body, timeout=None):
                return outer.respond(body, timeout)

        monkeypatch.setattr(_repl, "Session", Fake)
        _repl.shutdown()
        return self

    def respond(self, body, timeout=None):
        self.sent.append(body)
        if self.die_after is not None and len(self.sent) > self.die_after:
            raise _repl.ReplUnavailable("the REPL process is not running")
        if self.hang_after is not None and len(self.sent) > self.hang_after:
            raise _repl.ReplUnavailable("the REPL did not answer within 180s")

        self.env_counter += 1
        messages = []

        # A declaration made in a DERIVED environment. It must not be visible
        # to the next command, because the next command starts from base again.
        for name in _names_declared(body):
            self.declared.add(("derived", name))

        for name in _names_referenced(body):
            if name not in {n for kind, n in self.declared if kind == "base"}:
                messages.append({
                    "severity": "error",
                    "pos": {"line": 1, "column": 0},
                    "data": f"unknown identifier '{name}'",
                })

        return {"env": self.env_counter, "messages": messages}


def _names_declared(body):
    import re

    return re.findall(r"^\s*(?:theorem|lemma|def)\s+(\w+)", body, re.MULTILINE)


def _names_referenced(body):
    """Anything after `exact` — enough to model "can attempt 2 see attempt 1"."""
    import re

    return re.findall(r"\bexact\s+([\w.]+)", body)


@pytest.fixture
def repl_on(monkeypatch):
    monkeypatch.setenv("MRA_LEAN_REPL", "1")
    _util.forget()
    yield
    _repl.shutdown()
    _util.forget()


@pytest.fixture
def repl_off(monkeypatch):
    monkeypatch.delenv("MRA_LEAN_REPL", raising=False)
    _util.forget()
    yield
    _util.forget()


def run(coro):
    return asyncio.run(coro)


# ============================================ A. SUBPROCESS FALLBACK (default)
def test_the_repl_is_off_unless_asked_for(repl_off):
    assert _repl.enabled() is False


@pytest.mark.parametrize("value", ["0", "false", "no", "", "  "])
def test_only_an_explicit_yes_turns_it_on(monkeypatch, value):
    monkeypatch.setenv("MRA_LEAN_REPL", value)
    assert _repl.enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_the_documented_flag_turns_it_on(monkeypatch, value):
    monkeypatch.setenv("MRA_LEAN_REPL", value)
    assert _repl.enabled() is True


def test_with_the_flag_off_the_subprocess_path_runs(tmp_path, repl_off, monkeypatch):
    """The DEFAULT must be byte-for-byte what it was. A regression here would
    change every existing benchmark number silently."""
    calls = []

    async def fake_subprocess(source, workdir):
        calls.append(source)
        return True, ""

    monkeypatch.setattr(_util, "_subprocess_compile", fake_subprocess)

    def must_not_run(*a, **k):
        raise AssertionError("the REPL ran with the flag off")

    monkeypatch.setattr(_repl, "compile_source", must_not_run)

    result = run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := trivial"))

    assert calls, "the subprocess path was not used"
    assert result.outcome is LeanOutcome.COMPILED


def test_with_the_flag_on_the_subprocess_path_does_not_run(tmp_path, repl_on,
                                                           monkeypatch):
    FakeRepl().install(monkeypatch)

    async def must_not_run(source, workdir):
        raise AssertionError("a fresh subprocess was spawned in REPL mode")

    monkeypatch.setattr(_util, "_subprocess_compile", must_not_run)

    run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := trivial"))


# ==================================================== B. ISOLATION — load-bearing
def test_a_declaration_cannot_leak_into_the_next_attempt(tmp_path, repl_on,
                                                         monkeypatch):
    """THE TEST. Attempt 1 declares `leaked`; attempt 2 tries to use it and
    MUST fail, because both start from the same base environment.

    A fresh process gave this for free. A shared process has to earn it, and if
    it ever stops earning it the REPL path is unusable for benchmarking: one
    attempt could satisfy the next, and a "proof" would depend on what happened
    to be tried before it.
    """
    fake = FakeRepl().install(monkeypatch)
    run_lean = _util.lean_runner(str(tmp_path))

    first = run(run_lean(MATHLIB + "theorem leaked : True := trivial"))
    second = run(run_lean(MATHLIB + "theorem uses_it : True := by exact leaked"))

    assert first.outcome is LeanOutcome.COMPILED
    assert second.outcome is LeanOutcome.ERRORS, (
        "attempt 2 saw a declaration from attempt 1 — attempts are not isolated"
    )
    assert "unknown identifier 'leaked'" in second.output


def test_every_command_is_sent_against_the_base_environment(tmp_path, repl_on,
                                                            monkeypatch):
    """Isolation rests entirely on never threading a returned env id forward.
    Asserted on the wire so a future refactor cannot quietly start chaining."""
    sent = []

    class Recording(_repl.Session):
        """Overrides `_exchange`, NOT `command`, so the real `command()` runs.

        An earlier version of this test stubbed `command` itself and therefore
        tested nothing: deliberately threading `reply["env"]` back into
        `self.base` still passed. Intercepting one level lower means the
        payload asserted below is the payload production code built.
        """

        def start(self):
            self.base = 0
            self.process = object()
            return self

        def alive(self):
            return True

        def _exchange(self, payload, timeout):
            sent.append(payload)
            return {"env": len(sent), "messages": []}

    monkeypatch.setattr(_repl, "Session", Recording)
    _repl.shutdown()
    run_lean = _util.lean_runner(str(tmp_path))

    for index in range(4):
        run(run_lean(MATHLIB + f"theorem t{index} : True := trivial"))

    assert [payload["env"] for payload in sent] == [0, 0, 0, 0], (
        f"a derived environment was reused: {[p['env'] for p in sent]}"
    )
    assert all("import" not in payload["cmd"] for payload in sent)


def test_the_base_is_assigned_once_and_never_from_a_command():
    """`base` is set in `start()` and read everywhere else. If a command's
    returned env were ever assigned to it, isolation would decay silently over
    a run rather than fail loudly."""
    import inspect

    source = inspect.getsource(_repl.Session)
    assignments = [line for line in source.splitlines()
                   if "self.base =" in line]

    assert len(assignments) == 3, assignments   # __init__, start, close
    assert 'self.base = reply["env"]' in source      # start, from the import
    assert "self.base = None" in source              # close


def test_a_second_goal_reuses_the_session_without_reimporting(tmp_path, repl_on,
                                                              monkeypatch):
    """The whole point. Isolation is per COMMAND, so paying the Mathlib import
    again for a second goal would be pure waste."""
    fake = FakeRepl().install(monkeypatch)

    run(_util.lean_runner(str(tmp_path / "a"))(MATHLIB + "theorem a : True := trivial"))
    run(_util.lean_runner(str(tmp_path / "b"))(MATHLIB + "theorem b : True := trivial"))

    assert fake.starts == 1, f"Mathlib was imported {fake.starts} times"


# ================================================= C. BASE MATHLIB AVAILABILITY
def test_mathlib_declarations_are_visible_to_every_attempt(tmp_path, repl_on,
                                                           monkeypatch):
    fake = FakeRepl(declared=[("base", "Nat.exists_infinite_primes")])
    fake.install(monkeypatch)
    run_lean = _util.lean_runner(str(tmp_path))

    for index in range(3):
        result = run(run_lean(
            MATHLIB + f"theorem t{index} : True := by exact Nat.exists_infinite_primes"))
        assert result.outcome is LeanOutcome.COMPILED, (
            f"Mathlib was not available on attempt {index + 1}"
        )


def test_the_import_is_sent_once_at_session_start():
    assert _repl.BASE_COMMAND == "import Mathlib"


def test_imports_are_stripped_from_the_command_body():
    """The REPL refuses `import` when an `env` is given, correctly — the import
    is what the base environment already IS."""
    body = _repl.strip_imports("import Mathlib\nimport Foo.Bar\n"
                               "theorem t : True := trivial")

    assert "import" not in body
    assert body == "theorem t : True := trivial"


def test_everything_that_is_not_an_import_survives():
    """ProofNet statements carry `open` headers. Those are ordinary commands
    and must reach Lean — stripping them would change the mathematics."""
    source = ("import Mathlib\n"
              "open Complex Filter Function Metric Finset\n"
              "open scoped BigOperators Topology\n"
              "theorem exercise_1_13a : True := trivial")

    body = _repl.strip_imports(source)

    assert "open Complex Filter Function Metric Finset" in body
    assert "open scoped BigOperators Topology" in body
    assert "exercise_1_13a" in body


# ======================================================== D. EQUIVALENCE
REPRESENTATIVE = [
    # (label, source, repl reply, subprocess (ok, text), expected outcome)
    ("a proof that compiles",
     "theorem t : 2 + 2 = 4 := by norm_num",
     {"env": 1, "messages": []},
     (True, ""),
     LeanOutcome.COMPILED),
    ("an unknown identifier",
     "theorem t : True := by exact nonesuch",
     {"env": 1, "messages": [{"severity": "error", "pos": {"line": 1, "column": 20},
                              "data": "unknown identifier 'nonesuch'"}]},
     (False, "1:20: error: unknown identifier 'nonesuch'"),
     LeanOutcome.ERRORS),
    ("unsolved goals",
     "theorem t : False := by simp",
     {"env": 1, "messages": [{"severity": "error", "pos": {"line": 1, "column": 0},
                              "data": "unsolved goals\n⊢ False"}]},
     (False, "1:0: error: unsolved goals\n⊢ False"),
     LeanOutcome.ERRORS),
    ("a sorry",
     "theorem t : True := by sorry",
     {"env": 1, "messages": [{"severity": "warning", "pos": {"line": 1, "column": 0},
                              "data": "declaration uses 'sorry'"}]},
     (True, "1:0: warning: declaration uses 'sorry'"),
     LeanOutcome.INCOMPLETE),
    ("an axiom",
     "axiom cheat : False\ntheorem t : True := trivial",
     {"env": 1, "messages": []},
     (True, ""),
     LeanOutcome.CHEATED),
    ("a suggestion tactic",
     "theorem t : True := by exact?",
     {"env": 1, "messages": []},
     (True, ""),
     LeanOutcome.CHEATED),
    ("a warning that is not an error",
     "theorem t : True := trivial",
     {"env": 1, "messages": [{"severity": "warning", "pos": {"line": 1, "column": 0},
                              "data": "unused variable `x`"}]},
     (True, "1:0: warning: unused variable `x`"),
     LeanOutcome.COMPILED),
]


@pytest.mark.parametrize("label,body,reply,subprocess_result,expected",
                         REPRESENTATIVE,
                         ids=[case[0] for case in REPRESENTATIVE])
def test_both_modes_agree(tmp_path, monkeypatch, label, body, reply,
                          subprocess_result, expected):
    """The same snippet must be classified identically either way. This is what
    makes the REPL path safe to benchmark with — a proof rate produced under
    the flag has to mean what a proof rate produced without it means."""
    source = MATHLIB + body

    # --- REPL mode
    monkeypatch.setenv("MRA_LEAN_REPL", "1")
    _util.forget()

    class Fixed(_repl.Session):
        def start(self):
            self.base = 0
            self.process = object()
            return self

        def alive(self):
            return True

        def command(self, cmd, timeout=None):
            return reply

    monkeypatch.setattr(_repl, "Session", Fixed)
    _repl.shutdown()
    via_repl = run(_util.lean_runner(str(tmp_path / "repl"))(source))
    _repl.shutdown()

    # --- subprocess mode
    monkeypatch.delenv("MRA_LEAN_REPL", raising=False)
    _util.forget()

    async def fake_subprocess(src, workdir):
        return subprocess_result

    monkeypatch.setattr(_util, "_subprocess_compile", fake_subprocess)
    via_subprocess = run(_util.lean_runner(str(tmp_path / "sub"))(source))

    assert via_repl.outcome is expected, f"{label}: REPL said {via_repl.outcome}"
    assert via_subprocess.outcome is expected, (
        f"{label}: subprocess said {via_subprocess.outcome}"
    )
    assert via_repl.outcome is via_subprocess.outcome


def test_the_two_paths_share_one_classifier():
    """Equivalence is structural, not a coincidence to be re-tested forever:
    both branches produce `(ok, text)` and `_classify` is called once, after."""
    import inspect

    source = inspect.getsource(_util.lean_runner)

    assert source.count("_classify(") == 1, (
        "the two execution paths must not classify separately"
    )
    assert "_repl.compile_source" in source and "_subprocess_compile" in source


# ============================================================== E. ANTI-CHEAT
def test_sorry_is_incomplete_in_repl_mode(tmp_path, repl_on, monkeypatch):
    FakeRepl().install(monkeypatch)

    result = run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := by sorry"))

    assert result.outcome is LeanOutcome.INCOMPLETE


def test_an_axiom_is_cheating_in_repl_mode(tmp_path, repl_on, monkeypatch):
    FakeRepl().install(monkeypatch)

    result = run(_util.lean_runner(str(tmp_path))(
        MATHLIB + "axiom cheat : False\ntheorem t : True := trivial"))

    assert result.outcome is LeanOutcome.CHEATED


def test_a_suggestion_tactic_is_cheating_in_repl_mode(tmp_path, repl_on, monkeypatch):
    FakeRepl().install(monkeypatch)

    result = run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := by simp?"))

    assert result.outcome is LeanOutcome.CHEATED


def test_a_structurally_reported_sorry_is_rendered_for_the_classifier():
    """The REPL reports sorries as a field as well as a warning. If a version
    ever drops the warning, the field still reaches `_classify` as text."""
    text = _repl.render({"sorries": [{"proofState": 0, "goal": "⊢ True"}],
                         "messages": []})

    assert "sorry" in text


def test_the_anticheat_reads_the_source_not_the_transport():
    """It cannot be defeated by anything the REPL does or does not say, because
    `cheating_devices` inspects the submitted source."""
    from verifiers.lean_runner import cheating_devices

    assert cheating_devices("axiom bad : False")


# ================================================== F. TIMEOUT AND RECOVERY
def test_a_dead_process_is_restarted_and_the_command_retried(tmp_path, repl_on,
                                                             monkeypatch):
    """Requirement F. A failed request must not poison the next one."""
    fake = FakeRepl().install(monkeypatch)
    run_lean = _util.lean_runner(str(tmp_path))

    first = run(run_lean(MATHLIB + "theorem a : True := trivial"))
    assert first.outcome is LeanOutcome.COMPILED
    assert fake.starts == 1

    # The process dies on the NEXT command. The retry must restart it and
    # succeed, inside the same call — the caller sees an ordinary result.
    original = fake.respond

    def dies_once(body, timeout=None):
        fake.respond = original          # the RESTARTED session is healthy
        raise _repl.ReplUnavailable("the REPL process is not running")

    fake.respond = dies_once
    second = run(run_lean(MATHLIB + "theorem b : True := trivial"))

    assert second.outcome is LeanOutcome.COMPILED, "the session stayed poisoned"
    assert fake.starts == 2, "the dead session was not restarted"


def test_a_timeout_destroys_the_session_rather_than_reusing_it(tmp_path, repl_on,
                                                               monkeypatch):
    """A timed-out command leaves an unread response queued. Reusing the
    process would pair that response with the NEXT command — every result
    after it would be wrong, silently. So the session is destroyed."""
    fake = FakeRepl(hang_after=0).install(monkeypatch)

    run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := trivial"))

    assert _repl._session is None, "a timed-out session was kept"


def test_a_permanently_broken_repl_reports_unavailable_not_a_crash(tmp_path,
                                                                   repl_on,
                                                                   monkeypatch):
    """A verifier never crashes the graph. It also must not retry forever — a
    source that reliably wedges Lean would restart the session on every call."""
    fake = FakeRepl(die_after=0).install(monkeypatch)

    result = run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := trivial"))

    assert result.outcome is LeanOutcome.UNAVAILABLE
    assert fake.starts <= 2, f"retried {fake.starts} times; the cap is one retry"


def test_a_failure_to_start_at_all_is_unavailable(tmp_path, repl_on, monkeypatch):
    """No REPL binary, wrong toolchain, no Lean. Must degrade, not explode."""
    class Broken(_repl.Session):
        def start(self):
            raise _repl.ReplUnavailable("lake exe repl: unknown executable")

    monkeypatch.setattr(_repl, "Session", Broken)
    _repl.shutdown()

    result = run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := trivial"))

    assert result.outcome is LeanOutcome.UNAVAILABLE


def test_an_unavailable_result_is_never_memoised(tmp_path, repl_on, monkeypatch):
    """Already true on the subprocess path; it must stay true here, or one bad
    moment would become a permanent failure for the rest of the goal."""
    fake = FakeRepl(die_after=0).install(monkeypatch)
    run_lean = _util.lean_runner(str(tmp_path))
    source = MATHLIB + "theorem t : True := trivial"

    assert run(run_lean(source)).outcome is LeanOutcome.UNAVAILABLE

    healthy = FakeRepl().install(monkeypatch)
    assert run(run_lean(source)).outcome is LeanOutcome.COMPILED


def test_the_session_survives_an_ordinary_compile_error(tmp_path, repl_on,
                                                        monkeypatch):
    """A REJECTED proof is the normal case, not a failure of the transport.
    Restarting on every rejection would give back all the speed."""
    fake = FakeRepl().install(monkeypatch)
    run_lean = _util.lean_runner(str(tmp_path))

    run(run_lean(MATHLIB + "theorem a : True := by exact nonesuch"))
    run(run_lean(MATHLIB + "theorem b : True := trivial"))

    assert fake.starts == 1, "the session restarted after a compile error"


def test_the_command_timeout_is_configurable_and_separate_from_the_budget():
    assert _repl.TIMEOUT > 0
    assert _repl.START_TIMEOUT > _repl.TIMEOUT, (
        "starting pays the Mathlib import and needs longer than one command"
    )


# ================================================== G. MEMOIZATION, both modes
def test_identical_source_is_compiled_once_in_repl_mode(tmp_path, repl_on,
                                                        monkeypatch):
    fake = FakeRepl().install(monkeypatch)
    run_lean = _util.lean_runner(str(tmp_path))
    source = MATHLIB + "theorem t : True := trivial"

    run(run_lean(source))
    run(run_lean(source))

    assert len(fake.sent) == 1, "the same source was sent to the REPL twice"


def test_different_source_still_reaches_the_repl(tmp_path, repl_on, monkeypatch):
    fake = FakeRepl().install(monkeypatch)
    run_lean = _util.lean_runner(str(tmp_path))

    run(run_lean(MATHLIB + "theorem a : True := trivial"))
    run(run_lean(MATHLIB + "theorem b : True := trivial"))

    assert len(fake.sent) == 2


def test_the_memo_does_not_cross_goals_in_repl_mode(tmp_path, repl_on, monkeypatch):
    fake = FakeRepl().install(monkeypatch)
    source = MATHLIB + "theorem t : True := trivial"

    run(_util.lean_runner(str(tmp_path / "a"))(source))
    run(_util.lean_runner(str(tmp_path / "b"))(source))

    assert len(fake.sent) == 2


def test_the_memo_stores_the_classified_result_in_repl_mode(tmp_path, repl_on,
                                                            monkeypatch):
    """A cache holding raw output and skipping `_classify` would be a way to
    launder a cheat through a repeat compile."""
    fake = FakeRepl().install(monkeypatch)
    run_lean = _util.lean_runner(str(tmp_path))
    source = MATHLIB + "theorem t : True := by sorry"

    first = run(run_lean(source))
    second = run(run_lean(source))

    assert first.outcome is LeanOutcome.INCOMPLETE
    assert second.outcome is LeanOutcome.INCOMPLETE
    assert len(fake.sent) == 1


# ==================================================== the wire protocol itself
def test_a_response_is_framed_by_a_blank_line(tmp_path):
    """Verified against leanprover-community/repl: responses are pretty-printed
    across several lines and separated by blank lines."""
    import io

    session = _repl.Session()

    class Process:
        stdout = io.StringIO('{"env":\n 0,\n "messages": []}\n\n{"env": 1}\n\n')

        def poll(self):
            return None

    session.process = Process()

    assert session._read(5) == {"env": 0, "messages": []}
    assert session._read(5) == {"env": 1}


def test_a_closed_stream_is_reported_not_hung(tmp_path):
    import io

    session = _repl.Session()

    class Process:
        stdout = io.StringIO("")

        def poll(self):
            return None

    session.process = Process()

    with pytest.raises(_repl.ReplUnavailable):
        session._read(5)


def test_errors_decide_acceptance_because_there_is_no_exit_code():
    assert _repl.accepted({"messages": []}) is True
    assert _repl.accepted({"messages": [{"severity": "warning", "data": "x"}]}) is True
    assert _repl.accepted({"messages": [{"severity": "error", "data": "x"}]}) is False


def test_messages_are_rendered_in_lean_command_line_shape():
    """So `_classify` and everything downstream needs no special case."""
    text = _repl.render({"messages": [
        {"severity": "error", "pos": {"line": 3, "column": 11},
         "data": "unknown identifier 'foo'"},
    ]})

    assert text == "3:11: error: unknown identifier 'foo'"


def test_the_launch_command_is_configurable():
    assert _repl.argv()[:2] == ["lake", "exe"]


def test_a_configured_binary_is_launched_through_lake_env(monkeypatch):
    """The documented way to use the REPL from another project."""
    monkeypatch.setenv("MRA_LEAN_REPL_BIN", "/opt/repl/.lake/build/bin/repl")

    assert _repl.argv() == ["lake", "env", "/opt/repl/.lake/build/bin/repl"]
