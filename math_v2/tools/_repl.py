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

# Lines that must not be sent inside a command: the REPL rejects `import` when
# an `env` is specified, and the whole point is that the import already
# happened. Everything else in the source — `open`, `open scoped`, kept lemmas,
# the theorem — is an ordinary command and goes through untouched.
#
# THE WHOLE LINE, and a test says so. The first version of this matched
# `^\s*import\s+\S` and substituted only what it matched, so `import Mathlib`
# became `athlib` — a stray identifier sent to Lean at the top of every
# command. Caught by test_imports_are_stripped_from_the_command_body.
_IMPORT = re.compile(r"^[ \t]*import[ \t]+.*(?:\r?\n|$)", re.MULTILINE)

BASE_COMMAND = "import Mathlib"

# How long one command may take before the session is considered wedged.
# Separate from the agent's budget: this bounds ONE compile, exactly as
# `_local.run`'s `timeout=` does on the subprocess path.
TIMEOUT = float(os.getenv("MRA_LEAN_REPL_TIMEOUT", "180"))

# Starting the session pays the Mathlib import, so it gets its own, longer
# allowance. Measured cold on Windows: 116s.
START_TIMEOUT = float(os.getenv("MRA_LEAN_REPL_START_TIMEOUT", "600"))


def enabled():
    return os.getenv("MRA_LEAN_REPL", "").strip().lower() in ("1", "true", "yes")


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
        self._lock = threading.Lock()

    # ------------------------------------------------------------- lifecycle
    def start(self):
        """Spawn the process and pay for `import Mathlib` once."""
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
        reply = self._exchange({"cmd": BASE_COMMAND}, START_TIMEOUT)
        if "env" not in reply:
            raise ReplUnavailable(
                f"the REPL did not return a base environment: {str(reply)[:300]}"
            )
        self.base = reply["env"]
        return self

    def alive(self):
        return self.process is not None and self.process.poll() is None

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
            return self._exchange({"cmd": body, "env": self.base},
                                  timeout or TIMEOUT)


# One session per process. Isolation is per COMMAND, via `env`, so there is no
# reason to pay the Mathlib import again for a second goal — and every reason
# not to, since that import is the entire cost this module exists to remove.
_session = None
_session_lock = threading.Lock()


def session(cwd=None):
    global _session
    with _session_lock:
        if _session is not None and _session.alive():
            return _session
        if _session is not None:
            _session.close()
        _session = Session(cwd=cwd).start()
        return _session


def shutdown():
    """Stop the session. Called at the end of a benchmark, and by tests."""
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
        _session = None


def strip_imports(source):
    """The command body: everything but the `import` lines.

    The REPL refuses `import` when an `env` is given, which is correct — the
    import is what BASE already is.
    """
    return _IMPORT.sub("", source).strip()


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
    """Run one source. Returns `(ok, text)` — the same pair the subprocess path
    produces, so the caller classifies both identically.

    RECOVERY. A timeout or a dead process destroys the session and the command
    is retried ONCE on a fresh one. That covers the two cases that matter: a
    Lean process that wedged on a pathological elaboration, and one that was
    killed from outside. It retries once and not in a loop, because a source
    that reliably wedges Lean must not be able to restart the session forever —
    the second failure is reported as a real failure.
    """
    body = strip_imports(source)

    for attempt in (1, 2):
        try:
            live = await asyncio.to_thread(session, cwd)
            reply = await asyncio.to_thread(live.command, body, timeout)
            return accepted(reply), render(reply)
        except ReplUnavailable as exc:
            await asyncio.to_thread(shutdown)
            if attempt == 2:
                raise ReplUnavailable(str(exc))
    raise ReplUnavailable("unreachable")
