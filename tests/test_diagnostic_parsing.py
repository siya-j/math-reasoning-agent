"""Both Lean backends' diagnostics must parse. One of them never did.

WHY THIS FILE EXISTS
--------------------
`LeanResult.errors` splits compiler output into blocks on `_DIAGNOSTIC`, and
that pattern required a FILE PREFIX. The two backends do not format the same
way:

    subprocess   Claim.lean:5:2: error: unsolved goals
    REPL         5:2: error: unsolved goals

`math_v2/tools/_repl.py:560` builds the second, and its own comment says the
two carry "the same information" -- true, and never checked against the format
the consumer requires. So under the REPL backend NOTHING matched,
`errors` was always empty, and `interpret` fell back to `first_error`: the
literal first line of output.

MEASURED, on eval/results/putnam-run2.json, which ran on the REPL backend:

    goal   rejections telling the model NO error at all
    a4     15 of 24
    a6     13 of 15
    b1     10 of 17
    ----------------------------------------------------
    total  38 of 58  (66%)

On `putnam_1962_a4` the ENTIRE feedback for a rejected proof was

    41:97: warning: Variable name `x` is not explicitly referenced.

-- a cosmetic warning that happened to be the first line. The real error, and
the goal state after it, were discarded. That goal spent 18 attempts bouncing
off the same non-message; a6 wrote twenty consecutive skeletons.

The field's own ablations rank iterative refinement as the LARGEST contributor
to an agentic prover's performance. This agent had the structure of iterative
refinement and, two thirds of the time, refined against nothing.
"""

import pytest

from verifiers.lean_runner import LeanOutcome, LeanResult

ERROR_BODY = "case succ\nn : ℕ\n⊢ p (n + 1) = 0"

REPL = f"12:4: error: unsolved goals\n{ERROR_BODY}"
SUBPROCESS = f"Claim.lean:12:4: error: unsolved goals\n{ERROR_BODY}"
WINDOWS = (r"C:\Users\me\AppData\Local\Temp\tmp1\Claim.lean:12:4: error: "
           f"unsolved goals\n{ERROR_BODY}")


@pytest.mark.parametrize("label,output", [
    ("repl", REPL), ("subprocess", SUBPROCESS), ("windows", WINDOWS),
])
def test_every_backends_diagnostics_are_parsed(label, output):
    result = LeanResult(LeanOutcome.ERRORS, output)

    assert result.errors, f"{label}: no diagnostic block parsed at all"
    assert "unsolved goals" in result.errors[0]


@pytest.mark.parametrize("label,output", [
    ("repl", REPL), ("subprocess", SUBPROCESS), ("windows", WINDOWS),
])
def test_the_goal_state_travels_with_its_error(label, output):
    """The block, not the header line. Lean puts the useful part AFTER the
    error, and the goal state is the single most actionable thing a rejected
    attempt can be handed."""
    result = LeanResult(LeanOutcome.ERRORS, output)

    assert "⊢ p (n + 1) = 0" in result.errors[0], label


def test_a_leading_warning_does_not_swallow_the_error():
    """THE `putnam_1962_a4` CASE. A warning arriving first must not become the
    whole message -- that is exactly what happened 38 times, and it is why the
    agent looked like it could not learn from its errors."""
    output = ("41:97: warning: Variable name `x` is not explicitly referenced.\n"
              f"{REPL}")

    result = LeanResult(LeanOutcome.ERRORS, output)

    assert result.errors, "the warning swallowed the error again"
    assert "unsolved goals" in " ".join(result.errors)
    assert "not explicitly referenced" not in " ".join(result.errors), (
        "a cosmetic warning is being reported as a compiler error"
    )


def test_the_two_backends_parse_to_the_same_thing():
    """`_repl.py` claims the two paths carry "the same information". They do --
    and this asserts the CONSUMER agrees, which is the half that was missing.
    """
    repl = LeanResult(LeanOutcome.ERRORS, REPL)
    subprocess = LeanResult(LeanOutcome.ERRORS, SUBPROCESS)

    assert len(repl.errors) == len(subprocess.errors)
    assert repl.goals == subprocess.goals
    # The bodies differ only by the path prefix on the header line.
    assert repl.errors[0].split("error:")[1] == \
        subprocess.errors[0].split("error:")[1]


def test_output_with_no_diagnostic_still_falls_back_honestly():
    """`first_error` remains the fallback for output in no recognised format
    -- a timeout, a crashed toolchain. Returning nothing there would hide a
    real failure."""
    result = LeanResult(LeanOutcome.ERRORS, "error: no default toolchain configured")

    assert result.errors == []
    assert "no default toolchain" in result.first_error


def test_a_clean_compile_has_no_diagnostics():
    assert LeanResult(LeanOutcome.COMPILED, "").errors == []
    assert LeanResult(LeanOutcome.COMPILED, "").first_error == ""


# =====================================================================
# Driven through the REAL producer, not a string I typed
# =====================================================================
def test_the_repl_renderer_emits_something_the_parser_accepts():
    """THE test that would have caught this, and the one whose absence let it
    ship. Everything above uses hand-written output; this drives
    `_repl.render` -- the actual producer -- with a reply shaped like the
    REPL's own JSON, and asserts the consumer parses what comes out.

    `_repl.render`'s docstring asserted the two backends carried "the same
    information", and that was checked. Nobody checked that the format
    survived the trip to `LeanResult.errors`, which is where it died."""
    from math_v2.tools._repl import render

    reply = {"messages": [
        {"severity": "warning", "pos": {"line": 41, "column": 97},
         "data": "Variable name `x` is not explicitly referenced."},
        {"severity": "error", "pos": {"line": 12, "column": 4},
         "data": f"unsolved goals\n{ERROR_BODY}"},
    ]}

    result = LeanResult(LeanOutcome.ERRORS, render(reply))

    assert result.errors, "the real renderer still produces unparseable output"
    assert "unsolved goals" in " ".join(result.errors)
    assert "⊢ p (n + 1) = 0" in " ".join(result.errors), "goal state lost"
    assert "not explicitly referenced" not in " ".join(result.errors)


def test_a_repl_sorry_marker_is_also_parseable():
    """`render` synthesises `0:0: warning: declaration uses 'sorry'` for a
    structurally-reported sorry. That line has to survive the same trip, or a
    skeleton's own feedback goes missing too."""
    from math_v2.tools._repl import render

    text = render({"messages": [], "sorries": [{"pos": {"line": 1}}]})

    assert "declaration uses 'sorry'" in text
    # It is a warning, so it bounds a block without being reported as an error.
    assert LeanResult(LeanOutcome.INCOMPLETE, text).errors == []
