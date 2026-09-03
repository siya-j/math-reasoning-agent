"""Two anti-thrash rules the prompt already stated, now enforced in code.

WHY THIS EXISTS
---------------
MEASURED, on the PutnamBench run in `eval/results/putnam-run2.json`. Both
goals below had a full 40-compile budget and proved nothing with it.

  `putnam_1962_a6`  attempts 19-38: TWENTY consecutive skeletons, half the
                    budget, each a fresh decomposition rather than an attempt
                    at holes the previous one had already listed. The message
                    `try_skeleton` returns has begun with "DO NOT WRITE A NEW
                    SKELETON", in the imperative, naming the exact outstanding
                    claims, since the `hard-amgm-sqrt` failure. It said so
                    twenty times and was ignored twenty times.

  `putnam_1962_a4`  ten of thirty-five attempts byte-identical to an earlier
                    one. `try_proof` and `try_skeleton` have guarded repeats
                    since `exercise_1_26`; `try_lemma` never did.

The prompt's own rule -- "Two identical or near-identical resubmissions in a
row means you reacted to the surface of the error rather than to what it
meant" -- was already correct and already there. `prompt.py` says it best:
"a rule only stated here is a rule the model can decline."
"""

import asyncio

import pytest

from math_v2.core import log, proving
from verifiers.lean_runner import LeanOutcome, LeanResult

STATEMENT = "theorem mra_goal : 2 + 2 = 4"
SKELETON = "by\n  have h1 : 1 + 1 = 2 := by sorry\n  sorry"
OTHER_SKELETON = "by\n  have h2 : 2 + 0 = 2 := by sorry\n  sorry"
# A hole the automatic filler will actually attempt and close: `worth_proving`
# skips claims it judges trivial, so a claim that survives that filter is what
# makes the auto-fill path genuinely run.
FILLABLE = "by\n  have hstep : (7 : Nat) * 6 = 42 := by sorry\n  sorry"


@pytest.fixture
def workdir(tmp_path):
    return str(tmp_path)


def lean(outcome, output=""):
    seen = []

    async def run(source):
        seen.append(source)
        return LeanResult(outcome, output)

    run.seen = seen
    return run


def run(coro):
    return asyncio.run(coro)


def _skeleton_then_holes():
    """A compiler that answers a skeleton with `sorry` and a hole with success.

    The skeleton source still contains its holes, so the two are told apart by
    the source itself -- which is what the real compiler is doing too.
    """
    seen = []

    async def run(source):
        seen.append(source)
        if "sorry" in source:
            return LeanResult(LeanOutcome.ERRORS,
                              "warning: declaration uses `sorry`")
        return LeanResult(LeanOutcome.COMPILED, "")

    run.seen = seen
    return run


def typechecking_skeleton(workdir, proof=SKELETON):
    """Put a skeleton that typechecked-with-holes into the record.

    `declaration uses 'sorry'` is what Lean says when a skeleton holds
    together, and `try_skeleton` reads exactly that to decide `holds`.
    """
    return run(proving.try_skeleton(
        workdir, STATEMENT, proof,
        lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`"),
        fill_budget=0))


# ------------------------------------------------- try_lemma repeat guard
def test_a_repeated_lemma_is_refused_without_compiling(workdir):
    """MEASURED on `putnam_1962_a4`. The guard `try_proof` has had since
    `exercise_1_26`; a repeated lemma costs exactly what a repeated proof
    costs."""
    statement = "lemma helper (n : Nat) : n + 0 = n"
    first = lean(LeanOutcome.ERRORS, "error: unsolved goals")
    run(proving.try_lemma(workdir, statement, "by ring", first))
    assert len(first.seen) == 1

    second = lean(LeanOutcome.ERRORS, "error: unsolved goals")
    result = run(proving.try_lemma(workdir, statement, "by ring", second))

    assert result["ok"] is False
    assert result["error"] == "duplicate_attempt"
    assert second.seen == [], "a known-failed lemma reached the compiler"


def test_reformatting_a_failed_lemma_is_still_a_repeat(workdir):
    """`normalise` treats whitespace as no change, exactly as it does for a
    proof -- reformatting the same argument is the same argument."""
    statement = "lemma helper (n : Nat) : n + 0 = n"
    run(proving.try_lemma(workdir, statement, "by ring",
                          lean(LeanOutcome.ERRORS, "error: unsolved goals")))

    compiler = lean(LeanOutcome.ERRORS, "error: unsolved goals")
    result = run(proving.try_lemma(workdir, statement, "by    ring\n",
                                   compiler))

    assert result["error"] == "duplicate_attempt"
    assert compiler.seen == []


def test_a_changed_lemma_argument_is_not_a_repeat(workdir):
    """Negative control. Changing the argument IS a change, and the model is
    allowed to spend a compile discovering it did not help."""
    statement = "lemma helper (n : Nat) : n + 0 = n"
    run(proving.try_lemma(workdir, statement, "by ring",
                          lean(LeanOutcome.ERRORS, "error: unsolved goals")))

    compiler = lean(LeanOutcome.COMPILED)
    result = run(proving.try_lemma(workdir, statement, "by simp", compiler))

    assert compiler.seen, "a genuinely new argument was refused"
    assert result["outputs"]["accepted"] is True


def test_the_same_argument_against_a_different_lemma_is_not_a_repeat(workdir):
    """Also a negative control: the same tactic proves many different claims,
    and `already_tried` keys on the statement for that reason."""
    run(proving.try_lemma(workdir, "lemma one (n : Nat) : n + 0 = n", "by simp",
                          lean(LeanOutcome.ERRORS, "error: unsolved goals")))

    compiler = lean(LeanOutcome.COMPILED)
    run(proving.try_lemma(workdir, "lemma two (n : Nat) : 0 + n = n", "by simp",
                          compiler))

    assert compiler.seen, "a different lemma was refused as a repeat"


# ------------------------------------------------------ the skeleton loop
def test_a_second_skeleton_is_refused_while_the_first_is_untouched(workdir):
    """THE `putnam_1962_a6` regression: twenty consecutive skeletons."""
    typechecking_skeleton(workdir)

    compiler = lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`")
    result = run(proving.try_skeleton(workdir, STATEMENT, OTHER_SKELETON,
                                      compiler, fill_budget=0))

    assert result["ok"] is False
    assert result["error"] == "skeleton_not_engaged"
    assert compiler.seen == [], "a second skeleton reached the compiler"


def test_resubmitting_the_identical_skeleton_is_also_refused(workdir):
    """`already_tried` skips records whose status is TRUE, so a typechecking
    skeleton sent again verbatim reaches this guard uncaught -- and it is the
    same waste as a fresh one."""
    typechecking_skeleton(workdir)

    compiler = lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`")
    result = run(proving.try_skeleton(workdir, STATEMENT, SKELETON, compiler,
                                      fill_budget=0))

    assert result["error"] == "skeleton_not_engaged"
    assert compiler.seen == []


def test_the_refusal_names_the_claims_still_open(workdir):
    """A refusal that does not say what to do instead is just a wall."""
    typechecking_skeleton(workdir)

    result = run(proving.try_skeleton(
        workdir, STATEMENT, OTHER_SKELETON,
        lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`"),
        fill_budget=0))

    assert "1 + 1 = 2" in result["message"], result["message"]
    assert "try_lemma" in result["message"]


def test_the_first_skeleton_of_a_run_is_never_refused(workdir):
    """Negative control: with no decomposition on the table there is nothing
    to be avoiding."""
    compiler = lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`")
    result = run(proving.try_skeleton(workdir, STATEMENT, SKELETON, compiler,
                                      fill_budget=0))

    assert compiler.seen, "the first skeleton was refused"
    assert result["outputs"]["typechecks"] is True


def test_attempting_a_lemma_lifts_the_refusal(workdir):
    """Gated on the record, not on effort: one `try_lemma` of the model's own
    -- accepted or REJECTED -- lifts it. Re-deciding a decomposition after
    genuinely trying one of its claims is legitimate work."""
    typechecking_skeleton(workdir)
    run(proving.try_lemma(workdir, "lemma h1 : 1 + 1 = 2", "by norm_num",
                          lean(LeanOutcome.ERRORS, "error: unsolved goals")))

    compiler = lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`")
    result = run(proving.try_skeleton(workdir, STATEMENT, OTHER_SKELETON,
                                      compiler, fill_budget=0))

    assert compiler.seen, "a lemma attempt did not lift the refusal"
    assert result["outputs"]["typechecks"] is True


def test_a_skeleton_that_did_not_typecheck_does_not_block_the_next_one(workdir):
    """A broken decomposition must be fixable. Only one that HOLDS is a
    decomposition the model is being asked to engage with."""
    run(proving.try_skeleton(workdir, STATEMENT, SKELETON,
                             lean(LeanOutcome.ERRORS, "error: unsolved goals"),
                             fill_budget=0))

    compiler = lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`")
    run(proving.try_skeleton(workdir, STATEMENT, OTHER_SKELETON, compiler,
                             fill_budget=0))

    assert compiler.seen, "a rejected skeleton blocked its own repair"


def test_automatic_hole_filling_does_not_lift_the_refusal(workdir):
    """THE mechanism that would have made this guard useless, driven through
    the REAL auto-fill path rather than a hand-written record.

    `synthesize_lemmas` writes LEMMA records too. Counting those would let the
    system satisfy the condition on the model's behalf, and the loop would
    sustain itself through the very machinery meant to break it. Written this
    way on purpose: a first version of this test appended its own record with
    `auto=True` and therefore still passed when `synthesize_lemmas` stopped
    setting the flag at all -- it checked that the guard READS the flag, never
    that anything WRITES it.
    """
    compiler = _skeleton_then_holes()
    run(proving.try_skeleton(workdir, STATEMENT, FILLABLE, compiler,
                             fill_budget=4))

    synthesised = [r for r in log.records(workdir, log.LEMMA)]
    assert synthesised, "the auto-fill path did not run; the test proves nothing"
    assert all(r.get("auto") for r in synthesised), synthesised

    second = lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`")
    result = run(proving.try_skeleton(workdir, STATEMENT, OTHER_SKELETON,
                                      second, fill_budget=0))

    assert result["error"] == "skeleton_not_engaged"
    assert second.seen == []


def test_a_record_written_before_the_auto_field_reads_as_the_models_own(
        workdir):
    """Backward compatibility, stated as a test: an older log has no `auto`
    key, and `.get` reads that as falsy -- i.e. as the model's own work, the
    permissive direction. A guard must not retroactively refuse on the basis
    of a field that did not exist when the record was written."""
    log.append(workdir, log.Record(kind=log.SKELETON, statement=STATEMENT,
                                   proof=SKELETON, status=log.TRUE))
    data = log.read(workdir)
    data["records"].append({"kind": log.LEMMA, "statement": "lemma h : True",
                            "proof": "trivial", "status": log.TRUE})
    log._write(workdir, data)

    compiler = lean(LeanOutcome.ERRORS, "warning: declaration uses `sorry`")
    run(proving.try_skeleton(workdir, STATEMENT, OTHER_SKELETON, compiler,
                             fill_budget=0))

    assert compiler.seen, "a pre-existing record was read as automatic"
