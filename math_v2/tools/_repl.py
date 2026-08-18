"""A persistent Lean process, so Mathlib is imported once instead of per call.

NO `from __future__ import annotations` (blueprint §5.1, gotcha 1).

OPT-IN. `MRA_LEAN_REPL=1` and nothing else selects this; without it
`_util.lean_runner` behaves exactly as it did, byte for byte. Both paths stay
runnable so a disagreement between them is a number rather than an argument.

WHY
---
Measured on the target machine, steady state:

    empty file (process + lake startup)    13.6s
    import Mathlib                         40.5s
    import + one trivial theorem           39.5s

So ~39s of every compile is fixed overhead paid before Lean looks at the proof,
because each `lake env lean` is a fresh process with a fresh environment. Eight
compiles is five minutes of re-importing the same 8,690 .olean files. That is
not a property of the prover and it should not be in the measurement.

THE PROTOCOL, VERIFIED AGAINST leanprover-community/repl
--------------------------------------------------------
JSON on stdin and stdout, commands and responses separated by BLANK LINES.
Responses are pretty-printed across several lines, so a response is read by
accumulating until a blank line and parsing what came out — structured, never
scraped.

    {"cmd": "import Mathlib"}              ->  {"env": 0}        paid ONCE
    {"cmd": "theorem a ...", "env": 0}     ->  {"env": 1, "messages": [...]}
    {"cmd": "theorem b ...", "env": 0}     ->  {"env": 2, "messages": [...]}

ISOLATION — THE PROPERTY THIS MODULE LIVES OR DIES BY
-----------------------------------------------------
Every command carries `"env": BASE`, where BASE is the environment produced by
the single `import Mathlib`. From the REPL's documentation: the `env` field
"causes the command to be run in the existing environment", and "you can
backtrack simply by using earlier values for `env`". A command DERIVES a new
environment and returns a new id; it does not mutate the one it started from.

So attempt N+1, starting from BASE, cannot observe anything attempt N declared
— the same guarantee a fresh process gives, for a thousandth of the cost. The
one thing that would break it is threading a returned env id into a later
command, which this module never does: `BASE` is assigned once at session
start and is the only env value ever sent. `test_every_command_is_sent_against_
the_base_environment` asserts that on the wire, so a future refactor cannot
quietly start chaining.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No tactic mode, no proofState threading, no pickling. Command mode answers the
only question the agent asks — "does this source compile" — and every extra
feature is another way for state to leak between attempts.
"""

import asyncio
import json
import os
import re
import subprocess
import threading
import time

from math_v2 import _local

# IMPORT HANDLING. The session serves ONE import: `import Mathlib`, the base.
# Anything else is routed to `lake env lean` by `needs_subprocess` below, which
# is where the full history of getting this wrong is written down.
#
# These two patterns exist only to decide that routing, and to take the base
# import off the front of a command the REPL will not accept it in. Blank lines
# and `--` comments do not end an import block — Lean's rule, and the reason a
# real run's stray imports compiled on the subprocess path.
_IMPORT_LINE = re.compile(r"^[ \t]*import[ \t]+\S")
_COMMENT_OR_BLANK = re.compile(r"^[ \t]*(?:--.*)?$")

BASE_COMMAND = "import Mathlib"

# How long one command may take before the session is considered wedged.
# Separate from the agent's budget: this bounds ONE compile, exactly as
# `_local.run`'s `timeout=` does on the subprocess path.
TIMEOUT = float(os.getenv("MRA_LEAN_REPL_TIMEOUT", "180"))

# Starting the session pays the Mathlib import, so it gets its own, longer
# allowance. Measured cold on Windows: 116s.
START_TIMEOUT = float(os.getenv("MRA_LEAN_REPL_START_TIMEOUT", "600"))

# SESSION RECYCLING, against unbounded memory.
#
# Every command creates a NEW environment which the REPL retains — env ids
# simply increment and there is no command to free one. One session holding
# Mathlib is 4-6 GB before any of that. Over a 183-goal split at ~8 attempts
# each that is ~1,500 retained environments, and a benchmark that dies at goal
# 140 with an out-of-memory kill is worse than one that pays a 35s import every
# so often.
#
# Deliberately NOT tuned. 200 is a conservative guess; the threshold is here to
# be measured later, not to be optimal now.
MAX_COMMANDS = int(os.getenv("MRA_LEAN_REPL_MAX_COMMANDS", "200"))

# Asks the running Lean what version it is. Compared against the project's
# lean-toolchain at session start, because a REPL built for a different Lean
# fails in ways that look like mathematics: unknown identifiers, elaboration
# errors, statements that "do not elaborate". A benchmark can be entirely
# ruined by it and read as a bad proof rate.
VERSION_COMMAND = "#eval Lean.versionString"
_VERSION = re.compile(r"(\d+\.\d+\.\d+(?:-\w+)?)")


def enabled():
    """Is the REPL backend selected? The decision lives in `_local`."""
    return _local.lean_backend() == _local.REPL


def project_toolchain(project=None):
    """The Lean version the Lake project pins, or "" if it cannot be read."""
    project = project or _local.LEAN_PROJECT
    if not project:
        return ""
    try:
        with open(os.path.join(project, "lean-toolchain"), encoding="utf-8") as f:
            found = _VERSION.search(f.read())
    except OSError:
        return ""
    return found.group(1) if found else ""


def argv():
    """How to launch the REPL.

    `MRA_LEAN_REPL_BIN` points at a built `repl` binary, which is the
    documented way to use it from another project:
        lake env /path/to/repl/.lake/build/bin/repl
    Without it we assume the REPL is a dependency of the Lean project itself
    and `lake exe repl` resolves.
    """
    binary = os.getenv("MRA_LEAN_REPL_BIN", "").strip()
    if binary:
        return ["lake", "env", binary]
    return ["lake", "exe", "repl"]


class ReplUnavailable(Exception):
    """The session could not be started or could not be recovered."""


class Session(object):
    """One long-lived Lean process with Mathlib already loaded.

    Not thread-safe by construction — `_lock` serialises access, because a
    single stdin/stdout pair cannot serve two commands at once and interleaving
    them would pair the wrong response with the wrong request.
    """

    def __init__(self, cwd=None):
        self.cwd = cwd
        self.process = None
        self.base = None
        self.commands = 0
        self.version = ""
        self._startup = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------- lifecycle
    def start(self):
        """Spawn the process and pay for `import Mathlib` once."""
        began = time.time()
        self._spawn()
        reply = self._exchange({"cmd": BASE_COMMAND}, START_TIMEOUT)
        if "env" not in reply:
            raise ReplUnavailable(
                f"the REPL did not return a base environment: {str(reply)[:300]}"
            )
        self.base = reply["env"]
        self._check_version()
        self._startup = time.time() - began
        return self

    def _spawn(self):
        self.process = subprocess.Popen(
            argv(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.cwd,
            text=True,
            # Windows decoded Lean's UTF-8 output as cp1252 and crashed a whole
            # run once already (bug 19). It must not come back through a second
            # execution path.
            encoding="utf-8", errors="replace",
            bufsize=1,
        )

    def _check_version(self):
        """Refuse a REPL built against a different Lean than the project pins.

        A mismatch does not announce itself. It produces unknown identifiers
        and elaboration failures — indistinguishable, in a results file, from
        the agent being bad at mathematics. A whole benchmark can be spent on
        it. So it is checked once, at startup, and reported as what it is: a
        setup error, never a proof failure.
        """
        expected = project_toolchain(self.cwd)
        reply = self._exchange({"cmd": VERSION_COMMAND, "env": self.base}, TIMEOUT)
        found = _VERSION.search(
            " ".join(str(m.get("data", "")) for m in (reply.get("messages") or []))
        )
        self.version = found.group(1) if found else ""

        if expected and self.version and self.version != expected:
            raise ReplUnavailable(
                "SETUP ERROR, not a proof failure: the REPL binary is Lean "
                f"{self.version} but the project pins {expected}. Rebuild the "
                "REPL against the project's lean-toolchain:\n"
                "    cp <project>/lean-toolchain <repl>/lean-toolchain\n"
                "    cd <repl> && lake build\n"
                "Results from a mismatched REPL look like mathematical "
                "failures and are not usable."
            )

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def exhausted(self):
        """Has this session run enough commands to be recycled?"""
        return MAX_COMMANDS > 0 and self.commands >= MAX_COMMANDS

    def consume_startup(self):
        """The one-time import cost, reported ONCE.

        `budget.record_lean_seconds` learns what a compile costs here so the
        reserve can hold back enough for one. The session-start import is not
        a compile — counting it would teach the budget that a compile costs 35s
        when it costs 0.2s, and the reserve would hold back a quarter of the
        clock for the rest of the run.

        It still costs wall-clock time, and the wall clock is measured
        independently from `budget.reset`, so this excludes it from the
        steady-state ESTIMATE without excusing it from the deadline.
        """
        spent, self._startup = self._startup, 0.0
        return spent

    def close(self):
        if self.process is None:
            return
        try:
            self.process.kill()
            self.process.wait(timeout=10)
        except Exception:  # noqa: BLE001 - closing must never raise
            pass
        finally:
            self.process = None
            self.base = None

    # -------------------------------------------------------------- the wire
    def _exchange(self, payload, timeout):
        """Send one command, read one response. Raises on timeout or death."""
        if self.process is None or self.process.poll() is not None:
            raise ReplUnavailable("the REPL process is not running")

        try:
            self.process.stdin.write(json.dumps(payload) + "\n\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ReplUnavailable(f"could not write to the REPL: {exc}")

        return self._read(timeout)

    def _read(self, timeout):
        """Accumulate lines until they parse as JSON, or the deadline passes.

        Responses are pretty-printed across many lines and separated by blank
        lines, so a blank line is the frame boundary. Parsing is attempted at
        each boundary rather than assumed, which keeps this correct if the
        REPL ever emits a blank line inside an object.
        """
        collected = []
        result = {}
        error = []

        def pump():
            for line in self.process.stdout:
                if line.strip():
                    collected.append(line)
                    continue
                if not collected:
                    continue
                try:
                    result.update(json.loads("".join(collected)))
                except ValueError:
                    collected.append(line)
                    continue
                return
            error.append("the REPL closed its output")

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        reader.join(timeout)

        if reader.is_alive():
            # The process is mid-computation with an unread response queued.
            # Reusing it would pair that response with the NEXT command, so the
            # session is destroyed and the caller restarts. See `compile`.
            raise ReplUnavailable(f"the REPL did not answer within {timeout:.0f}s")
        if error:
            raise ReplUnavailable(error[0])
        return result

    # ----------------------------------------------------------------- usage
    def command(self, body, timeout=None):
        """Run one command in a fresh environment derived from BASE."""
        with self._lock:
            self.commands += 1
            return self._exchange({"cmd": body, "env": self.base},
                                  timeout or TIMEOUT)

    def run_source(self, source, timeout=None):
        """Run one assembled Lean source against the base environment.

        The caller has already checked `needs_subprocess(source)`, so the only
        leading import here is the base one and there is no import anywhere
        else. Every command therefore carries `"env": BASE` — the isolation
        property, unchanged and unqualified.
        """
        with self._lock:
            self.commands += 1
            imports, body = split_imports(source)
            if extra_imports(imports):
                # Unreachable through `compile_source`. If a future caller
                # reaches it, fail loudly rather than quietly serving a source
                # this session cannot represent faithfully.
                raise ReplUnavailable(
                    "this source needs imports the session cannot provide; "
                    "`needs_subprocess` should have routed it to `lake env lean`"
                )
            return self._exchange({"cmd": body, "env": self.base},
                                  timeout or TIMEOUT)


# One session per process. Isolation is per COMMAND, via `env`, so there is no
# reason to pay the Mathlib import again for a second goal — and every reason
# not to, since that import is the entire cost this module exists to remove.
_session = None
_session_lock = threading.Lock()


def session(cwd=None):
    """The live session, started or RECYCLED as needed.

    Recycling is transparent: the caller gets a working session either way and
    the result semantics do not change, because a fresh session rebuilds the
    same base environment from the same import. What it cannot do is carry
    state across — a recycled session is a new process, so isolation is if
    anything stronger, never weaker.
    """
    global _session
    with _session_lock:
        if _session is not None and _session.alive() and not _session.exhausted():
            return _session
        if _session is not None:
            _session.close()
        _session = Session(cwd=cwd).start()
        return _session


def describe():
    """What ran, for the results file. A benchmark number that cannot be
    attributed to a backend is not a measurement."""
    return {
        "lean_backend": _local.lean_backend(),
        "execution_mode": "local" if _local.enabled() else "dispatch",
        "lean_project": _local.LEAN_PROJECT or "",
        "lean_toolchain": project_toolchain(),
        "repl_binary": os.getenv("MRA_LEAN_REPL_BIN", "") if enabled() else "",
        "repl_version": (_session.version if _session is not None else ""),
        "repl_max_commands": MAX_COMMANDS if enabled() else None,
    }


def shutdown():
    """Stop the session. Called at the end of a benchmark, and by tests."""
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
        _session = None


def split_imports(source):
    """`(leading imports, the rest)`.

    The leading block is the run of import lines before the first declaration.
    Blank lines and `--` comments do NOT end it — that is Lean's rule, and it
    is why a real run's

        import Mathlib

        import Mathlib.Topology.Order
        import Mathlib.Data.Real.Basic

        theorem extreme_value_theorem ...

    compiled on the subprocess path. An import after a declaration is NOT
    leading, stays in `the rest`, and is rejected by Lean exactly as it would
    be in a file.
    """
    lines = source.splitlines(keepends=True)
    imports, index = [], 0
    for index, line in enumerate(lines):
        if _IMPORT_LINE.match(line):
            imports.append(line.strip())
            continue
        if _COMMENT_OR_BLANK.match(line.rstrip("\r\n")):
            continue
        break
    else:
        index = len(lines)
    return imports, "".join(lines[index:]).strip()


def extra_imports(imports):
    """Leading imports the base environment does not already provide.

    `import Mathlib` is the base by construction. Anything else this session
    cannot serve — see `needs_subprocess`.
    """
    return [line for line in imports if line != BASE_COMMAND]


def needs_subprocess(source):
    """Must this source go to a fresh `lake env lean` instead of the session?

    TRUE FOR ANY IMPORT THAT IS NOT A LEADING `import Mathlib`, and this is
    the third attempt at import handling. The first two both tried to make the
    REPL do it:

      v1  strip the import lines with a regex that matched only part of the
          line -> `import Mathlib` became a stray `athlib`.

      v2  strip every import line, anywhere, silently. MORE PERMISSIVE THAN
          LEAN: `import Mathlib.Does.Not.Exist` vanished and the file compiled
          where the subprocess reported `unknown module`.

      v3  send the extra imports to the live session as a real import command
          with no `env`, so Lean would resolve them. This REGRESSED a case
          that had been working:

              import Mathlib
              import Mathlib.Topology.Order
              import Mathlib.Data.Real.Basic
              theorem cmp_j : True := trivial

              subprocess -> compiled     repl -> errors

          Both modules exist — the subprocess compiled the identical source,
          and this exact header came out of a real run. So the failure is not
          module resolution; it is that a second `import` command in an
          already-running REPL session does not behave like the first. The
          README permits `import` only when no `env` is given and says nothing
          about doing it twice.

    Rather than guess at the REPL's import semantics a fourth time, a source
    that needs anything beyond the base import goes to the SUBPROCESS. That is
    not a fallback in the apologetic sense: it is the same compiler, given the
    same file, and it is therefore identical to the subprocess arm BY
    CONSTRUCTION rather than by our reasoning about it. All four required
    behaviours — valid single import, valid multiple imports, invalid import,
    import after a declaration — come out right because Lean decides all four,
    reading the file itself.

    The cost is honest and small. Of the 35 sources a real near-Mathlib run
    produced, 2 carried extra imports: ~6% of compiles pay the full ~40s, and
    94% keep the ~1s session path. Buying exactness for 6% of the compiles is
    a good trade; being subtly more permissive than Lean is not.
    """
    imports, body = split_imports(source)
    if extra_imports(imports):
        return True
    # An import after a declaration is a Lean error. It stays in the body, and
    # rather than rely on the session reporting it the same way, Lean reads it.
    return any(_IMPORT_LINE.match(line) for line in body.splitlines())


def strip_imports(source):
    """Backwards-compatible: the command body alone."""
    return split_imports(source)[1]


def render(reply):
    """REPL messages as Lean CLI text, so `_classify` needs no special case.

    THE POINT OF THIS FUNCTION. The anti-cheat, the placeholder check and the
    outcome vocabulary all live in `_util._classify`, which reads compiler
    output as text. Rendering the structured reply into the same shape Lean's
    command line produces means both execution paths reach the SAME classifier
    with the same information — which is what makes the equivalence tests
    meaningful rather than a comparison of two different code paths.
    """
    lines = []
    for message in reply.get("messages") or []:
        position = message.get("pos") or {}
        lines.append(
            "{line}:{column}: {severity}: {data}".format(
                line=position.get("line", 0),
                column=position.get("column", 0),
                severity=message.get("severity", "information"),
                data=message.get("data", ""),
            )
        )
    # A `sorry` reported structurally but without a warning message would
    # otherwise reach `_classify` as clean output. It also catches it in the
    # source, but saying it here keeps the two paths' TEXT equivalent too.
    for _ in reply.get("sorries") or []:
        lines.append("0:0: warning: declaration uses 'sorry'")
    return "\n".join(lines)


def accepted(reply):
    """Did it compile? The REPL has no exit code, so errors are the signal."""
    return not any(
        (message.get("severity") or "") == "error"
        for message in (reply.get("messages") or [])
    )


async def compile_source(source, cwd=None, timeout=None):
    """Run one source. Returns `(ok, text, startup_seconds)`.

    The first two match the subprocess path exactly, so the caller classifies
    both identically. The third is the one-time session import, reported so it
    can be excluded from the steady-state compile-cost estimate — see
    `Session.consume_startup`.

    RECOVERY. A timeout or a dead process destroys the session and the command
    is retried ONCE on a fresh one. That covers the two cases that matter: a
    Lean process that wedged on a pathological elaboration, and one that was
    killed from outside. It retries once and not in a loop, because a source
    that reliably wedges Lean must not be able to restart the session forever —
    the second failure is reported as a real failure.
    """
    for attempt in (1, 2):
        try:
            live = await asyncio.to_thread(session, cwd)
            startup = live.consume_startup()
            reply = await asyncio.to_thread(live.run_source, source, timeout)
            return accepted(reply), render(reply), startup
        except ReplUnavailable as exc:
            await asyncio.to_thread(shutdown)
            if attempt == 2:
                raise ReplUnavailable(str(exc))
    raise ReplUnavailable("unreachable")
