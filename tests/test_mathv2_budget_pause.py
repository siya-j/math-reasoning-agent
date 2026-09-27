"""A resumed run is not charged for the time it was paused. Offline.

THE FAILURE, MEASURED in Aura (2026-09-25..27): a run was checkpointed, the
machine slept for two days, and the resumed run's first tool call found

    time budget spent (155776s of 900s)

so it ended without doing anything after the resume. The budget's clock is
`time.time()` in the workspace file, which keeps running while nothing is.
`budget.PAUSE_GAP_SECONDS` is the fix: a gap between two budget writes longer
than any single operation can take is a pause, and is left out.
"""

import time

import pytest

from math_v2.core import budget


@pytest.fixture()
def clock(monkeypatch):
    """`time.time()` as budget sees it, moved forward on request."""
    offset = [0.0]
    real = time.time
    monkeypatch.setattr(budget.time, "time", lambda: real() + offset[0])

    def advance(seconds):
        offset[0] += seconds

    return advance


def test_a_run_resumed_after_two_days_still_has_its_budget(tmp_path, clock):
    workdir = str(tmp_path)
    budget.reset(workdir)
    assert budget.spend(workdir) is None          # one tool call before the pause

    clock(2 * 24 * 3600)                          # asleep for two days

    assert budget.spend(workdir) is None, "the resumed run was refused on time"
    assert budget.elapsed(workdir) < 60
    assert budget.remaining(workdir) > budget.MAX_SECONDS - 60
    spent = budget.summary(workdir)
    assert spent["paused_seconds"] >= 2 * 24 * 3600
    assert spent["terminated_early"] is False


def test_the_pause_is_recorded_once_not_on_every_read(tmp_path, clock):
    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.spend(workdir)
    clock(10_000)
    budget.spend(workdir)                         # persists the discount
    first = budget.summary(workdir)["paused_seconds"]
    budget.spend(workdir)
    assert budget.summary(workdir)["paused_seconds"] == first


def test_ordinary_time_between_calls_is_still_charged(tmp_path, clock):
    """A gap shorter than PAUSE_GAP_SECONDS is work -- a model call, a
    compile -- and must count, or the time budget stops bounding anything."""
    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.spend(workdir)
    clock(budget.PAUSE_GAP_SECONDS - 60)
    budget.spend(workdir)
    assert budget.elapsed(workdir) >= budget.PAUSE_GAP_SECONDS - 60
    assert budget.summary(workdir)["paused_seconds"] == 0


def test_a_budget_spent_before_the_pause_stays_spent(tmp_path, clock):
    """Only the pause is forgiven, not the work done before it."""
    workdir = str(tmp_path)
    budget.reset(workdir)
    budget.spend(workdir)
    clock(budget.MAX_SECONDS + 10)                # spent, but under the pause gap
    stop = budget.spend(workdir)
    if budget.MAX_SECONDS + 10 < budget.PAUSE_GAP_SECONDS:
        assert stop is not None and stop["limit"] == "time"
