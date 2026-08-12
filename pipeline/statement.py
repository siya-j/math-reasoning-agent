"""Does the STATEMENT elaborate, before anyone tries to prove it?

WHY THIS EXISTS
---------------
Measured on `lin-vector-space-basis`. The formalizer produced

    theorem exists_basis (K : Type u) (V : Type v) [DivisionRing K]
        [AddCommGroup V] [Module K V] : ∃ (ι : Type v), Nonempty (Basis ι K V)

and every attempt failed with the same error — including `by sorry`:

    error: Function expected at Basis, but this term has type ?m.1
    Hint: The identifier `Basis` is unknown, and Lean's `autoImplicit`
    option causes an unknown identifier to be treated as an implicitly
    bound variable

`Basis` was renamed to `Module.Basis`. The statement could never compile, so
every compilation spent on it was spent proving nothing.

TWO SEPARATE PROBLEMS, AND THIS FIXES BOTH
------------------------------------------
1. MEASUREMENT. That run reported a formalisation rate of 100%, because
   "formalised" meant "the model returned a non-empty string". A formalizer
   failure was scored against the prover. This is the same shape as failures
   11 and 17 in the log: a rate whose denominator is wrong will lie to you.

2. CAPABILITY. Formalisation was the one stage of the pipeline with NO
   feedback loop — a single call, never checked, never revised, and the
   acknowledged weak point of the whole system. Lean will say exactly what is
   wrong with a statement, and Loogle will supply the name that replaced the
   missing one. Neither was being asked.

WHAT IS DELIBERATELY NOT DONE HERE
----------------------------------
A repair may fix a NAME. It cannot be allowed to fix the MATHEMATICS: a
statement that is quietly weakened until it compiles is failure 3 and 8 all
over again, and this module has no way to detect that. The repair prompt
forbids it, which is a request rather than a guarantee — so a repaired
statement is recorded in the trace, plainly, for a human to read.
"""

from __future__ import annotations

import re

import config

# Lean says this two different ways depending on where the name appeared.
_UNKNOWN_NAME = re.compile(
    r"unknown identifier '([^']+)'|identifier [`'\"]([^`'\"]+)[`'\"] is unknown"
)


def unknown_identifiers(errors: str) -> list[str]:
    """The names Lean did not recognise, in order, without duplicates."""
    found: list[str] = []
    for match in _UNKNOWN_NAME.finditer(errors):
        name = match.group(1) or match.group(2)
        if name and name not in found:
            found.append(name)
    return found


def elaboration_errors(statement: str, runner=None) -> str:
    """Compiler errors in the STATEMENT itself. "" when it elaborates.

    Checked with `sorry` as the proof, so the signature is the only thing
    under test. `sorry` proves nothing, which is exactly what is wanted: the
    question is whether Lean can make sense of the claim, not whether it is
    true.

    A missing Lean, or a timeout, returns "" — unknown is not broken, and
    refusing to prove on a machine with no compiler would be absurd.
    """
    from verifiers.lean_runner import LeanOutcome, run_lean
    from verifiers.lean_verifier import build_source

    runner = runner or run_lean
    result = runner(build_source(statement, "sorry"))
    if result.outcome is not LeanOutcome.ERRORS:
        return ""
    return "\n".join(result.errors[:3]) or result.output[:600]


def name_hints(errors: str, search=None) -> str:
    """Ask Loogle what the unrecognised names were renamed to.

    Verified against the live service: `?q=Basis` returns an error whose
    suggestion list contains `Module.Basis`. The answer to this exact failure
    is one query away, and nothing was asking for it.
    """
    names = unknown_identifiers(errors)
    if not names or search is None:
        return ""

    lines = []
    for name in names[:3]:
        try:
            _, suggestions = search.search_with_suggestions(name)
        except Exception:  # noqa: BLE001 - retrieval must never break a run
            continue
        real = [s for s in suggestions if not s.startswith('"')]
        if real:
            lines.append(f"  {name} -> {', '.join(real[:6])}")

    if not lines:
        return ""
    return (
        "\nMathlib names that resemble the ones Lean did not recognise:\n"
        + "\n".join(lines)
    )


def ensure_elaborates(run, goal, formalizer, search=None, note=None,
                      checker=None, rounds=None) -> bool:
    """Check `run.statement`, repair it, and record what happened.

    Returns True if the statement elaborates. On False the caller should stop:
    no proof of an unelaborable statement exists to be found.

    Mutates `run` — `statement`, `statement_ok`, `telemetry` and the trace.

    WHY THIS IS A LOOP AND NOT A SINGLE ATTEMPT
    -------------------------------------------
    Lean reports what stopped it, not everything wrong. A statement can be
    wrong in several independent ways at once — an outdated name AND an
    undeclared universe — and fixing the first only reveals the second. One
    attempt can therefore only ever repair one KIND of fault.

    WHY EVERY ATTEMPT SEES ALL THE PREVIOUS ONES
    --------------------------------------------
    This is the lesson that produced the agentic prover, applied here. The
    baseline prover asked for a proof five times through a stateless call and
    got byte-identical proposals back, because a fresh mind given the same
    prompt makes the same mistake. A repair loop without history is that same
    failure with a different name.
    """
    if not config.CHECK_STATEMENT:
        return True

    checker = checker or elaboration_errors
    rounds = config.MAX_STATEMENT_REPAIRS if rounds is None else rounds

    def say(stage: str) -> None:
        if note:
            note(stage)

    say("checking the statement")
    errors = checker(run.statement)
    run.telemetry.lean_calls += 1
    if not errors:
        return True

    run.log("statement rejected", errors[:300])

    # Every rejected attempt, so a repair is never asked to guess blind twice.
    history: list[tuple[str, str]] = [(run.statement, errors)]
    seen = {run.statement.strip()}

    for attempt in range(rounds):
        say(f"repairing the statement ({attempt + 1}/{rounds})")

        hints = name_hints(errors, search)
        if hints:
            run.telemetry.retrieval_calls += 1

        try:
            repaired = formalizer.repair_statement(
                goal, run.statement, errors, hints, history=tuple(history)
            )
        except Exception as exc:  # noqa: BLE001
            run.log("repair failed", str(exc)[:200])
            break
        run.telemetry.model_calls += 1

        repaired = repaired.strip()
        if not repaired or repaired in seen:
            # The same answer again. More rounds will not help.
            run.log("repair repeated itself", repaired[:200])
            break
        seen.add(repaired)

        errors = checker(repaired)
        run.telemetry.lean_calls += 1
        if not errors:
            # Recorded loudly. A repair may fix a NAME; if it quietly fixed
            # the MATHEMATICS instead, this line is the only place a human
            # will see it.
            run.log("statement repaired", f"{run.statement}  ->  {repaired}")
            run.statement = repaired
            return True

        run.log(f"repair {attempt + 1} rejected", f"{repaired}\n{errors[:200]}")
        history.append((repaired, errors))

    run.statement_ok = False
    run.log("statement not elaborable", errors[:300])
    return False
