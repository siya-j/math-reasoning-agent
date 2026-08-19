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

    def __init__(self, *, declared=(), die_after=None, hang_after=None,
                 modules=("Mathlib.Topology.Order", "Mathlib.Data.Real.Basic",
                          "Mathlib.Order.Basic")):
        self.modules = set(modules)
        self.imported = []
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

            def _exchange(self, payload, timeout):
                # Import commands carry no `env`. A fake that resolves every
                # module would hide the very asymmetry these tests exist for,
                # so `outer.modules` decides what exists.
                if "env" not in payload:
                    return outer.resolve(payload["cmd"])
                return outer.respond(payload["cmd"], timeout)

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


    def resolve(self, command):
        """An import command, resolved the way Lean would: real modules give a
        new environment, a module that does not exist gives an error."""
        self.imported.append(command)
        wanted = [line.split()[1] for line in command.splitlines()
                  if line.strip().startswith("import")]
        missing = [name for name in wanted
                   if name != "Mathlib" and name not in self.modules]
        if missing:
            return {"messages": [{
                "severity": "error", "pos": {"line": 0, "column": 0},
                "data": f"unknown module prefix '{missing[0]}'"}]}
        self.env_counter += 1
        return {"env": 100 + self.env_counter, "messages": []}


def _names_declared(body):
    import re

    return re.findall(r"^\s*(?:theorem|lemma|def)\s+(\w+)", body, re.MULTILINE)


def _names_referenced(body):
    """Anything after `exact` — enough to model "can attempt 2 see attempt 1"."""
    import re

    return re.findall(r"\bexact\s+([\w.]+)", body)


@pytest.fixture
def repl_on(monkeypatch):
    monkeypatch.setenv("MRA_LEAN_BACKEND", "repl")
    _util.forget()
    yield
    _repl.shutdown()
    _util.forget()


@pytest.fixture
def repl_off(monkeypatch):
    monkeypatch.delenv("MRA_LEAN_BACKEND", raising=False)
    monkeypatch.delenv("MRA_LEAN_REPL", raising=False)
    _util.forget()
    yield
    _util.forget()


def run(coro):
    return asyncio.run(coro)


# ============================================ A. SUBPROCESS FALLBACK (default)
def test_the_repl_is_off_unless_asked_for(repl_off):
    assert _repl.enabled() is False


@pytest.mark.parametrize("value", ["subprocess", "", "  ", "nonsense"])
def test_only_an_explicit_yes_turns_it_on(monkeypatch, value):
    monkeypatch.setenv("MRA_LEAN_BACKEND", value)
    assert _repl.enabled() is False


@pytest.mark.parametrize("value", ["repl", "REPL", " repl "])
def test_the_documented_flag_turns_it_on(monkeypatch, value):
    monkeypatch.setenv("MRA_LEAN_BACKEND", value)
    assert _repl.enabled() is True


def test_with_the_flag_off_the_subprocess_path_runs(tmp_path, repl_off, monkeypatch):
    """The DEFAULT must be byte-for-byte what it was. A regression here would
    change every existing benchmark number silently."""
    calls = []

    async def fake_subprocess(source, workdir):
        calls.append(source)
        return True, "", 0.0

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
    monkeypatch.setenv("MRA_LEAN_BACKEND", "repl")
    _util.forget()

    class Fixed(_repl.Session):
        def start(self):
            self.base = 0
            self.process = object()
            return self

        def alive(self):
            return True

        # `_exchange`, not `command`: production goes through `run_source` so
        # that imports are handled, and a fake that intercepts higher up would
        # skip the code under test.
        def _exchange(self, payload, timeout):
            return reply

    monkeypatch.setattr(_repl, "Session", Fixed)
    _repl.shutdown()
    via_repl = run(_util.lean_runner(str(tmp_path / "repl"))(source))
    _repl.shutdown()

    # --- subprocess mode
    monkeypatch.delenv("MRA_LEAN_BACKEND", raising=False)
    monkeypatch.delenv("MRA_LEAN_REPL", raising=False)
    _util.forget()

    async def fake_subprocess(src, workdir):
        return subprocess_result + (0.0,)

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


# ================================ H. THE TWO AXES ARE INDEPENDENT (decision 1)
def test_exec_and_lean_backend_are_separate_knobs(monkeypatch):
    """`MRA_EXEC=repl` was rejected deliberately. `_local.enabled()` gates the
    SymPy worker's interpreter and dispatch as well as Lean, so overloading it
    would have sent every `check_numeric` through Aura on a host with no Aura
    — a silent break in symbolic computation, caused by a Lean setting."""
    monkeypatch.setenv("MRA_EXEC", "local")
    monkeypatch.setenv("MRA_LEAN_BACKEND", "repl")
    monkeypatch.setattr(_local, "MODE", "local")

    assert _local.enabled() is True, "the SymPy worker path was disturbed"
    assert _local.lean_backend() == "repl"
    assert _util.mode() == "local"
    assert _util.lean_backend() == "repl"


def test_the_repl_backend_does_not_change_where_the_worker_runs(monkeypatch):
    monkeypatch.setenv("MRA_LEAN_BACKEND", "repl")
    monkeypatch.setattr(_local, "MODE", "local")

    import sys

    assert _util.worker_argv("check_primality")[0] == sys.executable


def test_dispatch_mode_is_untouched_by_the_lean_backend(monkeypatch):
    monkeypatch.setenv("MRA_LEAN_BACKEND", "repl")
    monkeypatch.setattr(_local, "MODE", "dispatch")

    assert _local.enabled() is False
    assert _util.mode() == "dispatch"


def test_the_old_flag_still_selects_the_repl(monkeypatch):
    """Backwards compatibility. Runs recorded under MRA_LEAN_REPL=1 must stay
    reproducible."""
    monkeypatch.delenv("MRA_LEAN_BACKEND", raising=False)
    monkeypatch.setenv("MRA_LEAN_REPL", "1")

    assert _local.lean_backend() == "repl"


def test_an_explicit_backend_beats_the_alias(monkeypatch):
    monkeypatch.setenv("MRA_LEAN_BACKEND", "subprocess")
    monkeypatch.setenv("MRA_LEAN_REPL", "1")

    assert _local.lean_backend() == "subprocess"


def test_the_default_is_the_subprocess(monkeypatch):
    monkeypatch.delenv("MRA_LEAN_BACKEND", raising=False)
    monkeypatch.delenv("MRA_LEAN_REPL", raising=False)

    assert _local.lean_backend() == "subprocess"


# ==================================== I. SESSION RECYCLING (decision 2)
class Recyclable(_repl.Session):
    """A Session with a real command counter and no process."""

    spawned = 0

    def _spawn(self):
        type(self).spawned += 1
        self.process = object()

    def _exchange(self, payload, timeout):
        if payload["cmd"] == _repl.BASE_COMMAND:
            return {"env": 0}
        if payload["cmd"] == _repl.VERSION_COMMAND:
            return {"messages": [{"severity": "info", "data": "4.33.0"}]}
        return {"env": 99, "messages": []}

    def alive(self):
        return self.process is not None

    def close(self):
        self.process = None


@pytest.fixture
def recyclable(monkeypatch):
    Recyclable.spawned = 0
    monkeypatch.setattr(_repl, "Session", Recyclable)
    monkeypatch.setattr(_repl, "MAX_COMMANDS", 3)
    _repl.shutdown()
    yield Recyclable
    _repl.shutdown()


def test_a_session_is_reused_until_the_threshold(recyclable):
    for _ in range(3):
        _repl.session().command("theorem t : True := trivial")

    assert recyclable.spawned == 1


def test_the_session_is_recycled_at_the_threshold(recyclable):
    """Every command creates an environment the REPL retains and cannot free.
    Over a 183-goal split that is ~1,500 of them on top of Mathlib's 4-6 GB."""
    for _ in range(4):
        _repl.session().command("theorem t : True := trivial")

    assert recyclable.spawned == 2, "the session was never recycled"


def test_recycling_rebuilds_the_base_environment(recyclable):
    for _ in range(4):
        live = _repl.session()
        live.command("theorem t : True := trivial")

    assert live.base == 0, "the recycled session lost its base environment"
    assert live.commands == 1, "the command counter did not reset"


def test_a_recycled_session_cannot_carry_state(recyclable):
    """A new process, so isolation is if anything stronger. What must NOT
    happen is a recycled session inheriting a derived environment id."""
    first = _repl.session()
    for _ in range(3):
        first.command("theorem a : True := trivial")
    second = _repl.session()

    assert second is not first
    assert second.base == 0
    assert first.process is None, "the old session was not closed"


def test_recycling_is_configurable_and_conservative_by_default(monkeypatch):
    import importlib

    monkeypatch.delenv("MRA_LEAN_REPL_MAX_COMMANDS", raising=False)
    reloaded = importlib.reload(_repl)
    try:
        assert reloaded.MAX_COMMANDS == 200
    finally:
        monkeypatch.setattr(_repl, "MAX_COMMANDS", reloaded.MAX_COMMANDS)


def test_recycling_can_be_switched_off(recyclable, monkeypatch):
    monkeypatch.setattr(_repl, "MAX_COMMANDS", 0)

    for _ in range(10):
        _repl.session().command("theorem t : True := trivial")

    assert recyclable.spawned == 1


def test_shutdown_closes_the_session(recyclable):
    live = _repl.session()
    _repl.shutdown()

    assert live.process is None
    assert _repl._session is None


# ============================= J. TOOLCHAIN VALIDATION (decision 5)
class Versioned(Recyclable):
    reported = "4.33.0"

    def _exchange(self, payload, timeout):
        if payload["cmd"] == _repl.VERSION_COMMAND:
            return {"messages": [{"severity": "info",
                                  "data": f'"{type(self).reported}"'}]}
        return super()._exchange(payload, timeout)


def write_toolchain(tmp_path, version):
    (tmp_path / "lean-toolchain").write_text(f"leanprover/lean4:v{version}\n")
    return str(tmp_path)


def test_the_project_toolchain_is_read(tmp_path):
    assert _repl.project_toolchain(write_toolchain(tmp_path, "4.33.0")) == "4.33.0"


def test_a_missing_toolchain_file_is_not_an_error(tmp_path):
    assert _repl.project_toolchain(str(tmp_path)) == ""


def test_a_matching_repl_starts(tmp_path, monkeypatch):
    Versioned.reported = "4.33.0"
    monkeypatch.setattr(_repl, "Session", Versioned)
    _repl.shutdown()

    live = _repl.session(write_toolchain(tmp_path, "4.33.0"))

    assert live.version == "4.33.0"
    _repl.shutdown()


def test_a_mismatched_repl_is_a_setup_error_not_a_proof_failure(tmp_path,
                                                                repl_on,
                                                                monkeypatch):
    """A REPL built for another Lean produces unknown identifiers and
    elaboration failures — in a results file, indistinguishable from the agent
    being bad at mathematics. A whole benchmark can be spent on it."""
    Versioned.reported = "4.20.0"
    monkeypatch.setattr(_repl, "Session", Versioned)
    _repl.shutdown()
    project = write_toolchain(tmp_path, "4.33.0")

    result = run(_util.lean_runner(project)(MATHLIB + "theorem t : True := trivial"))

    assert result.outcome is LeanOutcome.UNAVAILABLE
    assert "SETUP ERROR" in result.output
    assert "4.20.0" in result.output and "4.33.0" in result.output
    _repl.shutdown()


def test_an_unreadable_version_does_not_block_startup(tmp_path, monkeypatch):
    """Better to run than to refuse over a version we could not determine —
    the mismatch check is a safety net, not a gate."""
    class Silent(Recyclable):
        def _exchange(self, payload, timeout):
            if payload["cmd"] == _repl.VERSION_COMMAND:
                return {"messages": []}
            return super()._exchange(payload, timeout)

    monkeypatch.setattr(_repl, "Session", Silent)
    _repl.shutdown()

    live = _repl.session(write_toolchain(tmp_path, "4.33.0"))
    assert live.base == 0
    _repl.shutdown()


# ============================ K. BUDGET ACCOUNTING (decision 6)
def test_the_one_time_import_is_excluded_from_the_compile_estimate(tmp_path,
                                                                   repl_on,
                                                                   monkeypatch):
    """The reserve holds back enough for ONE compile. Teaching it that a
    compile costs 35s when it costs 0.2s would hold back a quarter of every
    budget for the rest of the run."""
    from math_v2.core import budget

    fake = FakeRepl().install(monkeypatch)

    async def slow_start(source, cwd=None, timeout=None):
        return True, "", 40.0          # 40s of it was the Mathlib import

    monkeypatch.setattr(_repl, "compile_source", slow_start)
    budget.reset(str(tmp_path))

    run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := trivial"))

    assert budget.read(str(tmp_path))["slowest_lean"] == 0.0, (
        "the session import was counted as compile cost"
    )


def test_startup_still_counts_against_the_wall_clock(tmp_path):
    """Excluded from the ESTIMATE, not excused from the DEADLINE. The wall
    clock runs from `budget.reset` and never from the compile timer."""
    import inspect

    from math_v2.core import budget

    assert "started" in inspect.getsource(budget._over)
    assert "record_lean_seconds" not in inspect.getsource(budget._over)


def test_the_subprocess_path_reports_no_startup_to_exclude(tmp_path, repl_off,
                                                           monkeypatch):
    """A fresh process has no amortised startup — every second of it IS the
    compile, and excluding any of it would understate the real cost."""
    from math_v2.core import budget

    async def fake_subprocess(source, workdir):
        import time as t

        t.sleep(0.05)
        return True, "", 0.0

    monkeypatch.setattr(_util, "_subprocess_compile", fake_subprocess)
    budget.reset(str(tmp_path))

    run(_util.lean_runner(str(tmp_path))(MATHLIB + "theorem t : True := trivial"))

    assert budget.read(str(tmp_path))["slowest_lean"] > 0


# ================================ L. OBSERVABILITY (decisions 3 and 4)
def test_the_trace_reports_the_two_dimensions_separately(tmp_path, monkeypatch):
    """`local+repl` would read as a third execution mode. It is not one."""
    from math_v2 import harness
    from math_v2.core import budget

    monkeypatch.setenv("MRA_LEAN_BACKEND", "repl")
    monkeypatch.setattr(_local, "MODE", "local")
    monkeypatch.setattr(budget, "MAX_SECONDS", 60.0)

    class Agent:
        async def ainvoke(self, payload, context=None):
            return {"messages": [{"role": "assistant", "content": "done"}]}

    run_result = harness.prove("q", model=object(), workdir=str(tmp_path),
                               agent_factory=lambda m, t, p: Agent())

    assert "execution mode: local" in run_result.trace
    assert "lean backend: repl" in run_result.trace
    assert not any("local+repl" in entry for entry in run_result.trace)


def test_the_trace_reports_the_subprocess_backend_too(tmp_path, repl_off,
                                                      monkeypatch):
    from math_v2 import harness
    from math_v2.core import budget

    monkeypatch.setattr(_local, "MODE", "local")
    monkeypatch.setattr(budget, "MAX_SECONDS", 60.0)

    class Agent:
        async def ainvoke(self, payload, context=None):
            return {"messages": [{"role": "assistant", "content": "done"}]}

    run_result = harness.prove("q", model=object(), workdir=str(tmp_path),
                               agent_factory=lambda m, t, p: Agent())

    assert "lean backend: subprocess" in run_result.trace


def test_the_backend_is_recorded_in_the_results_file(tmp_path, repl_on, monkeypatch):
    """A results file that cannot be attributed to a backend is not a
    measurement. The two arms of the A/B differ by ~8x in wall clock."""
    import config
    from eval.proof_dataset import Tier
    from eval.proof_metrics import ProofOutcome, ProofResult, summarize
    from scripts.evaluate_proofs import save

    monkeypatch.setattr(config, "PROVER", "math_v2")
    monkeypatch.setattr(_local, "MODE", "local")
    out = tmp_path / "run.json"
    results = [ProofResult(goal_id="g", area="a", tier=Tier.PROOFNET,
                           outcome=ProofOutcome.PROVED)]

    save(results, summarize(results), out)
    written = json.loads(out.read_text())

    assert written["environment"]["lean_backend"] == "repl"
    assert written["environment"]["execution_mode"] == "local"


def test_the_description_names_everything_needed_to_reproduce(repl_on, monkeypatch):
    monkeypatch.setenv("MRA_LEAN_REPL_BIN", "/opt/repl/.lake/build/bin/repl")
    monkeypatch.setattr(_local, "MODE", "local")

    where = _repl.describe()

    for key in ("lean_backend", "execution_mode", "lean_project",
                "lean_toolchain", "repl_binary", "repl_version",
                "repl_max_commands"):
        assert key in where, key
    assert where["repl_binary"].endswith("repl")


def test_the_subprocess_description_does_not_claim_a_repl(repl_off, monkeypatch):
    monkeypatch.setattr(_local, "MODE", "local")

    where = _repl.describe()

    assert where["lean_backend"] == "subprocess"
    assert where["repl_binary"] == ""
    assert where["repl_max_commands"] is None


# ============================ M. IMPORT SEMANTICS MATCH LEAN (the 15th snippet)
#
# MEASURED on the deterministic gate, and the one row of fifteen that failed:
#
#     import Mathlib.Does.Not.Exist
#     theorem cmp_k : True := trivial
#
#     subprocess -> errors     (unknown module)
#     repl       -> compiled   (strip_imports removed the line)
#
# The REPL was accepting a file Lean rejects. These tests pin the fix.

def test_a_leading_import_of_mathlib_is_the_base(tmp_path, repl_on, monkeypatch):
    """The overwhelming majority. One command against BASE, no extra import."""
    fake = FakeRepl().install(monkeypatch)

    result = run(_util.lean_runner(str(tmp_path))(
        "import Mathlib\ntheorem t : True := trivial"))

    assert result.outcome is LeanOutcome.COMPILED
    assert fake.imported == [], "an unnecessary import was paid for"
    assert fake.sent == ["theorem t : True := trivial"]


def compiled_by(monkeypatch, source, tmp_path, subprocess_result=(True, "", 0.0)):
    """Run one source in REPL mode and report WHICH path served it."""
    fake = FakeRepl().install(monkeypatch)
    took_subprocess = []

    async def capture(src, workdir):
        took_subprocess.append(src)
        return subprocess_result

    monkeypatch.setattr(_util, "_subprocess_compile", capture)
    result = run(_util.lean_runner(str(tmp_path))(source))
    return result, ("subprocess" if took_subprocess else "repl"), fake, took_subprocess


def test_a_valid_single_leading_import_matches_the_subprocess(tmp_path, repl_on,
                                                              monkeypatch):
    """REGRESSION. `import Mathlib.Topology.Order` compiled on the subprocess
    path and ERRORED in the REPL, because v3 sent a second import command to an
    already-running session. Both modules exist; the failure was ours."""
    result, path, fake, written = compiled_by(
        monkeypatch,
        "import Mathlib\nimport Mathlib.Topology.Order\n\ntheorem t : True := trivial",
        tmp_path)

    assert path == "subprocess", "the session tried to serve an extra import again"
    assert result.outcome is LeanOutcome.COMPILED
    assert written[0].startswith("import Mathlib"), "the source was rewritten"


def test_valid_multiple_leading_imports_match_the_subprocess(tmp_path, repl_on,
                                                             monkeypatch):
    """The exact header a real run produced, and the row that regressed."""
    result, path, fake, written = compiled_by(
        monkeypatch,
        "import Mathlib\n\nimport Mathlib.Topology.Order\n"
        "import Mathlib.Data.Real.Basic\n\ntheorem cmp_j : True := trivial",
        tmp_path)

    assert path == "subprocess"
    assert result.outcome is LeanOutcome.COMPILED
    assert "Mathlib.Topology.Order" in written[0]
    assert "Mathlib.Data.Real.Basic" in written[0]


def test_an_invalid_leading_import_is_an_error(tmp_path, repl_on, monkeypatch):
    """Lean reports `unknown module`; so must we. Not stripped, not ignored."""
    result, path, fake, _ = compiled_by(
        monkeypatch,
        "import Mathlib\nimport Mathlib.Does.Not.Exist\ntheorem t : True := trivial",
        tmp_path,
        subprocess_result=(False, "error: unknown module prefix 'Mathlib.Does'", 0.0))

    assert path == "subprocess"
    assert result.outcome is LeanOutcome.ERRORS
    assert "unknown module" in result.output


def test_the_bare_base_import_still_uses_the_session(tmp_path, repl_on, monkeypatch):
    """The point of all this. ~94% of real sources look like this and must keep
    the fast path — routing everything to the subprocess would be correct and
    useless."""
    result, path, fake, _ = compiled_by(
        monkeypatch, "import Mathlib\ntheorem t : True := trivial", tmp_path)

    assert path == "repl"
    assert fake.sent == ["theorem t : True := trivial"]
    assert result.outcome is LeanOutcome.COMPILED


def test_routing_is_decided_by_the_source_alone():
    """Deterministic and offline — no session, no Lean, no model."""
    assert _repl.needs_subprocess("import Mathlib\ntheorem t : True := trivial") is False
    assert _repl.needs_subprocess("theorem t : True := trivial") is False
    for source in (
        "import Mathlib\nimport Mathlib.Order.Basic\ntheorem t : True := trivial",
        "import Mathlib\nimport Mathlib.Nope\ntheorem t : True := trivial",
        "import Mathlib\ntheorem a : True := trivial\nimport Mathlib.Late",
    ):
        assert _repl.needs_subprocess(source) is True, source


def test_the_session_refuses_a_source_it_cannot_represent(tmp_path):
    """Belt and braces. If a future caller bypasses the routing, fail loudly
    rather than quietly serving a source with the imports missing."""
    session = _repl.Session()
    session.base = 0
    session.process = object()

    with pytest.raises(_repl.ReplUnavailable):
        session.run_source("import Mathlib\nimport Mathlib.Order.Basic\n"
                           "theorem t : True := trivial")


def test_a_non_leading_import_goes_to_lean_itself(tmp_path, repl_on, monkeypatch):
    """Requirement 4. It must behave like the subprocess rather than silently
    disappearing — so Lean reads the file and says what it thinks."""
    fake = FakeRepl().install(monkeypatch)
    written = []

    async def capture(source, workdir):
        written.append(source)
        return False, "error: invalid 'import' command", 0.0

    monkeypatch.setattr(_util, "_subprocess_compile", capture)
    source = ("import Mathlib\ntheorem a : True := trivial\n"
              "import Mathlib.Data.Real.Basic")

    result = run(_util.lean_runner(str(tmp_path))(source))

    assert result.outcome is LeanOutcome.ERRORS
    assert written == [source], "the source was rewritten before Lean saw it"
    assert fake.sent == [], "the session served a source it cannot represent"


def test_comments_and_blank_lines_do_not_end_the_import_block():
    """Lean's rule, and the reason a real run's stray imports compiled."""
    imports, body = _repl.split_imports(
        "import Mathlib\n\n-- a note\nimport Mathlib.Order.Basic\n\n"
        "theorem t : True := trivial")

    assert imports == ["import Mathlib", "import Mathlib.Order.Basic"]
    assert body == "theorem t : True := trivial"


def test_a_declaration_ends_the_import_block():
    imports, body = _repl.split_imports(
        "import Mathlib\ntheorem a : True := trivial\nimport Mathlib.Late")

    assert imports == ["import Mathlib"]
    assert "import Mathlib.Late" in body


def test_the_base_import_is_not_re_requested():
    assert _repl.extra_imports(["import Mathlib"]) == []
    assert _repl.extra_imports(
        ["import Mathlib", "import Mathlib.Order.Basic"]
    ) == ["import Mathlib.Order.Basic"]


def test_the_subprocess_path_is_untouched_by_any_of_this(tmp_path, repl_off,
                                                         monkeypatch):
    """It writes the source to a file verbatim and lets Lean read it. Nothing
    above applies, and nothing above may change it."""
    written = []

    async def capture(source, workdir):
        written.append(source)
        return True, "", 0.0

    monkeypatch.setattr(_util, "_subprocess_compile", capture)
    source = "import Mathlib\nimport Mathlib.Nope\ntheorem t : True := trivial"

    run(_util.lean_runner(str(tmp_path))(source))

    assert written == [source], "the subprocess path saw a rewritten source"


# ============ N. THE DEBUG SCRIPT AND THE GATE MUST BE THE SAME THING
#
# MEASURED FAILURE OF METHOD, not of code. `debug_one_source.py` retyped the
# gate's source and reported AGREE while the gate reported DISAGREE on the same
# commit. The string turned out to be identical, but that could not be known
# without checking — and a debugging tool that might not be reproducing the
# bug is worse than none, because it produces confident false negatives.
#
# So the case and the compile path are now IMPORTED, and these tests make it
# impossible for them to drift apart again.
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
TARGET_ROW = "a stray import the model wrote"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    import sys as _sys

    _sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_debug_script_defines_no_case_of_its_own():
    """It must not retype a source. One canonical definition, imported."""
    text = (SCRIPTS / "debug_one_source.py").read_text(encoding="utf-8")

    assert "import Mathlib.Topology.Order" not in text, (
        "the debug script hard-codes a Lean source again"
    )
    assert "clm.SNIPPETS" in text or "SNIPPETS" in text
    assert "compare_lean_modes" in text


def test_the_debug_script_reuses_the_gate_compile_path():
    """`compile_all` and `in_mode` are the gate's own functions, so the two
    scripts cannot execute different pipelines."""
    text = (SCRIPTS / "debug_one_source.py").read_text(encoding="utf-8")

    assert "clm.compile_all" in text, "the debug script reimplements compiling"
    assert "clm.in_mode" in text, "the debug script reimplements backend selection"


def test_both_scripts_resolve_the_same_source_for_the_target_row():
    """The assertion the first version needed and did not have."""
    clm = load_script("compare_lean_modes")
    debug = load_script("debug_one_source")

    label, body = next(s for s in clm.SNIPPETS if s[0] == TARGET_ROW)
    from_gate = clm.MATHLIB + body

    assert debug.TARGET == TARGET_ROW
    matched = [s for s in debug.gate().SNIPPETS if debug.TARGET in s[0]]
    assert matched, "the debug script cannot find the row it targets"
    assert debug.gate().MATHLIB + matched[0][1] == from_gate


def test_the_target_row_routes_to_lean_itself():
    """Offline and exact. No session, no Lean, no ambiguity."""
    clm = load_script("compare_lean_modes")
    label, body = next(s for s in clm.SNIPPETS if s[0] == TARGET_ROW)
    source = clm.MATHLIB + body

    assert _repl.needs_subprocess(source) is True

    imports, rest = _repl.split_imports(source)
    assert imports == ["import Mathlib",
                       "import Mathlib.Topology.Order",
                       "import Mathlib.Data.Real.Basic"]
    assert rest == "theorem cmp_j : True := trivial"


def test_the_target_row_reaches_lean_unmodified(tmp_path, repl_on, monkeypatch):
    """Both arms hand `_subprocess_compile` the SAME text."""
    clm = load_script("compare_lean_modes")
    label, body = next(s for s in clm.SNIPPETS if s[0] == TARGET_ROW)
    source = clm.MATHLIB + body

    fake = FakeRepl().install(monkeypatch)
    seen = []

    async def capture(src, workdir):
        seen.append(src)
        return True, "", 0.0

    monkeypatch.setattr(_util, "_subprocess_compile", capture)
    result = run(_util.lean_runner(str(tmp_path))(source))

    assert seen == [source], "the source was altered before Lean saw it"
    assert fake.sent == [], "the session was asked to serve it"
    assert result.outcome is LeanOutcome.COMPILED


def test_routing_does_not_depend_on_what_ran_before(tmp_path, repl_on, monkeypatch):
    """SEQUENCE was the only untested variable between the two scripts. The
    routing decision is a pure function of the source, and must stay one."""
    clm = load_script("compare_lean_modes")
    label, body = next(s for s in clm.SNIPPETS if s[0] == TARGET_ROW)
    source = clm.MATHLIB + body

    FakeRepl().install(monkeypatch)
    decisions = [_repl.needs_subprocess(source)]

    for _, earlier in clm.SNIPPETS[:9]:
        run(_util.lean_runner(str(tmp_path))(clm.MATHLIB + earlier))
        decisions.append(_repl.needs_subprocess(source))

    assert set(decisions) == {True}, (
        f"routing changed as the run progressed: {decisions}"
    )


# ===== O. TWO LEAN PROCESSES CANNOT BOTH HOLD MATHLIB
#
# THE ROOT CAUSE, found after four wrong attempts at import handling. A routed
# source shelled out to `lake env lean` while the session still had the whole
# library memory-mapped, and Lean reported:
#
#     error: failed to read file '...\Mathlib\AlgebraicTopology\SimplicialSet\
#            AnodyneExtensions\UnionProd.olean.private'
#
# It was never about imports. It was about two processes.

def test_the_session_is_released_before_a_routed_compile(tmp_path, repl_on,
                                                         monkeypatch):
    """THE REGRESSION, in the order the gate runs it:
    REPL commands first -> an import-bearing source routed to the subprocess
    -> it must compile, which means no session may be alive when it does."""
    clm = load_script("compare_lean_modes")
    fake = FakeRepl().install(monkeypatch)
    alive_during_shellout = []

    async def capture(source, workdir):
        alive_during_shellout.append(
            _repl._session is not None and _repl._session.alive())
        return True, "", 0.0

    monkeypatch.setattr(_util, "_subprocess_compile", capture)
    runner = _util.lean_runner(str(tmp_path))

    # Rows 1-9 of the gate: ordinary sources, served by the session.
    for _, body in clm.SNIPPETS[:9]:
        _util.forget()
        run(runner(clm.MATHLIB + body))
    assert fake.starts == 1, "the session never started, so this proves nothing"
    assert _repl._session is not None and _repl._session.alive()

    # Row 10: carries extra imports, so it is routed to `lake env lean`.
    _util.forget()
    label, body = clm.SNIPPETS[9]
    result = run(runner(clm.MATHLIB + body))

    assert alive_during_shellout == [False], (
        "the session was still holding Mathlib while lake env lean ran"
    )
    assert result.outcome is LeanOutcome.COMPILED


def test_a_routed_compile_does_not_end_the_run(tmp_path, repl_on, monkeypatch):
    """Releasing the session must be transparent. The next session-eligible
    source starts a fresh one — the recycling path, already proved stateless."""
    fake = FakeRepl().install(monkeypatch)

    async def capture(source, workdir):
        return True, "", 0.0

    monkeypatch.setattr(_util, "_subprocess_compile", capture)
    runner = _util.lean_runner(str(tmp_path))

    run(runner(MATHLIB + "theorem a : True := trivial"))            # session
    _util.forget()
    run(runner(MATHLIB + "import Mathlib.Order.Basic\ntheorem b : True := trivial"))
    _util.forget()
    result = run(runner(MATHLIB + "theorem c : True := trivial"))   # session again

    assert result.outcome is LeanOutcome.COMPILED
    assert fake.starts == 2, "the session did not come back after being released"


def test_a_fresh_session_after_release_carries_nothing(tmp_path, repl_on,
                                                       monkeypatch):
    """A released session is a dead process, so isolation is if anything
    stronger. What must not happen is a stale env id surviving."""
    fake = FakeRepl().install(monkeypatch)

    async def capture(source, workdir):
        return True, "", 0.0

    monkeypatch.setattr(_util, "_subprocess_compile", capture)
    runner = _util.lean_runner(str(tmp_path))

    run(runner(MATHLIB + "theorem leaked : True := trivial"))
    first = _repl._session
    _util.forget()
    run(runner(MATHLIB + "import Mathlib.Order.Basic\ntheorem b : True := trivial"))
    _util.forget()
    run(runner(MATHLIB + "theorem uses_it : True := by exact leaked"))

    assert _repl._session is not first
    assert _repl._session.base == 0


def test_releasing_is_a_no_op_when_no_session_exists():
    _repl.shutdown()
    assert _repl.release_for_subprocess() is False


def test_releasing_reports_that_it_closed_something(recyclable):
    _repl.session()
    assert _repl.release_for_subprocess() is True
    assert _repl._session is None


def test_the_subprocess_backend_never_releases_anything(tmp_path, repl_off,
                                                        monkeypatch):
    """With the REPL off there is no session to release, and the default path
    must not gain a step it does not need."""
    called = []
    monkeypatch.setattr(_repl, "release_for_subprocess",
                        lambda: called.append(True))

    async def capture(source, workdir):
        return True, "", 0.0

    monkeypatch.setattr(_util, "_subprocess_compile", capture)
    run(_util.lean_runner(str(tmp_path))(
        MATHLIB + "import Mathlib.Order.Basic\ntheorem t : True := trivial"))

    assert called == []
