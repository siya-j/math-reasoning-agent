"""The tactic ladder is not the model engaging with the goal.

`harness._stopped_short` asks "did the agent stop without really trying", and
answered it by counting every `kind=PROOF` record. But
`proving.try_standard_tactics` writes one of those too -- it compiles Lean's
`first | rfl | trivial | simp | ...` ladder plus every retrieved premise in a
single invocation, and it is the SYSTEM's attempt, not the model's.

MEASURED across the 141 preserved workdirs:

  * 75 of 212 proof records -- 35% -- are the ladder.
  * 32 unproved runs had enough proof records to stand the guard down;
    14 OF THOSE 32 had fewer than three MODEL attempts.
  * The worst case, `mathv2_zbeoyjt0`, has ELEVEN ladder attempts and NONE
    from the model. It stopped voluntarily, with an empty `reason` in its
    budget file, and nothing prodded it. It is also the run with the most
    submitted proof text in the whole corpus: busy, but never at the goal.

This is the same substitution `log.Record.auto` was introduced to prevent one
level down, where automatic hole-filling answered "has the model engaged with
its own decomposition" on the model's behalf.
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from math_v2 import harness
from math_v2.core import budget, log


def _workdir():
    path = tempfile.mkdtemp()
    log.clear(path)
    budget.reset(path)
    return path


def _attempt(workdir, auto):
    log.append(workdir, log.Record(
        kind=log.PROOF, statement="theorem t : 1 = 1", proof="by rfl",
        status=log.UNKNOWN, auto=auto))


def test_the_ladder_marks_its_own_records():
    """Producer side. If `try_standard_tactics` stops setting the flag, its
    attempts silently become the model's again."""
    source = (Path(harness.__file__).parent / "core" / "proving.py").read_text(
        encoding="utf-8")
    body = source[source.index("async def try_standard_tactics"):]
    body = body[:body.index("\nasync def", 10)]
    assert "auto=True" in body, (
        "try_standard_tactics no longer marks its records as the system's")


def test_ladder_attempts_do_not_count_as_engagement():
    workdir = _workdir()
    for _ in range(11):
        _attempt(workdir, auto=True)
    assert len(log.records(workdir, log.PROOF)) == 11
    assert harness._model_attempts(workdir) == 0


def test_model_attempts_do_count():
    workdir = _workdir()
    _attempt(workdir, auto=True)
    _attempt(workdir, auto=False)
    _attempt(workdir, auto=False)
    assert harness._model_attempts(workdir) == 2


def test_a_record_predating_the_flag_reads_as_the_model_s():
    """The safe direction: it under-fires rather than prodding a goal that
    was genuinely worked. Every record in `eval/evidence/` predates this."""
    workdir = _workdir()
    log.append(workdir, log.Record(
        kind=log.PROOF, statement="t", proof="p", status=log.UNKNOWN))
    assert harness._model_attempts(workdir) == 1


def test_the_eleven_ladder_case_now_fires():
    """`mathv2_zbeoyjt0`, reconstructed: eleven ladder attempts, none from
    the model. Under the old rule 11 >= 3 stood the guard down."""
    workdir = _workdir()
    for _ in range(11):
        _attempt(workdir, auto=True)
    assert harness._model_attempts(workdir) < harness.ENGAGEMENT_FLOOR


def test_two_real_attempts_stand_the_guard_down():
    """85% of proofs arrive within two MODEL attempts, so a goal that made
    two has reached where attempts stop converting."""
    workdir = _workdir()
    for _ in range(2):
        _attempt(workdir, auto=False)
    assert harness._model_attempts(workdir) >= harness.ENGAGEMENT_FLOOR


def test_the_floor_matches_the_model_only_curve():
    """RE-DERIVED. Three came from "83% within three attempts" computed over
    every proof record. On model attempts alone the same 85% arrives at two,
    and the third attempt adds nothing (85% to 85%)."""
    assert harness.ENGAGEMENT_FLOOR == 2


def test_both_call_sites_use_the_model_count():
    """The decision and the message it prints must agree; a message quoting
    a different number than the guard acted on is worse than no message."""
    source = Path(harness.__file__).read_text(encoding="utf-8")
    body = "\n".join(l for l in source.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "len(log.records(workdir, log.PROOF))" not in body, (
        "a site still counts every proof record, ladder included")
    assert body.count("_model_attempts(workdir)") >= 2


def test_the_verdict_still_counts_every_proof():
    """SOUNDNESS BOUNDARY. A ladder proof is a real proof -- 5 of 46 accepted
    proofs in the corpus arrived with zero model attempts. Only the
    ENGAGEMENT question excludes them; deciding whether the goal was proved
    must not."""
    verdict = (Path(harness.__file__).parent / "core" / "verdict.py").read_text(
        encoding="utf-8")
    assert "log.PROOF" in verdict
    assert "auto" not in verdict, (
        "verdict must not filter by `auto`; a ladder proof is still a proof")
