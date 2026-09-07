"""What a run cost to BILL, as opposed to what it cost in calls.

WHY THIS EXISTS
---------------
`Telemetry` has always counted `model_calls`, and on this path that is a poor
proxy for money. `math_v2/harness.py`'s own docstring records why: "There is
no summarisation, self-validation or narration middleware in this path", so
the whole message history rides along on every call and the input side grows
with the run.

MEASURED, on eval/results/putnam-run2.json: 13 model calls on the goal that
bailed out early against 53 on the hardest. That is 4x the calls, but with
input growing every turn it is on the order of 16x the input tokens -- and
the run's real constraint is the API bill, which nothing in the repo could
read. A cost you cannot measure is a cost you cannot optimise against.

0 IS "NOT REPORTED", NEVER "FREE". The baseline prover populates none of this
and some providers return no usage metadata at all; both must read as silence
rather than as a run that cost nothing. That is why `summarize` reports None
and the rendered report prints "not reported".
"""

from domain.proof import ProofRun, Telemetry, Verdict, VerificationStatus
from eval.proof_dataset import Goal, Tier
from eval.proof_metrics import ProofResult, render, result_from, summarize
from math_v2 import harness


class FakeUsage:
    """A message shaped like the LangChain standard: `usage_metadata` dict."""

    def __init__(self, kind, usage=None):
        self.type = kind
        self.content = ""
        if usage is not None:
            self.usage_metadata = usage


# ------------------------------------------------------ reading the usage
def test_tokens_are_summed_across_the_transcript():
    result = {"messages": [
        FakeUsage("ai", {"input_tokens": 100, "output_tokens": 10}),
        FakeUsage("tool"),
        FakeUsage("ai", {"input_tokens": 250, "output_tokens": 40}),
    ]}

    assert harness._count_tokens(result) == (350, 50)


def test_a_transcript_without_usage_metadata_reports_zero_not_a_guess():
    """Deliberately NOT estimated from string lengths. A fabricated count that
    looks authoritative is worse than an honest zero."""
    result = {"messages": [FakeUsage("ai"), FakeUsage("ai")]}

    assert harness._count_tokens(result) == (0, 0)


def test_a_missing_or_odd_transcript_does_not_raise():
    """Cost reporting must never be able to fail a run that proved something."""
    for bad in (None, {}, {"messages": None}, {"messages": []}, "text"):
        assert harness._count_tokens(bad) == (0, 0)


def test_partial_usage_is_counted_not_discarded():
    result = {"messages": [
        FakeUsage("ai", {"input_tokens": 7}),
        FakeUsage("ai", {"output_tokens": 3}),
    ]}

    assert harness._count_tokens(result) == (7, 3)


# ------------------------------------------------- through to the results
def _run_with(input_tokens, output_tokens):
    run = ProofRun(goal="g")
    run.statement = "theorem t : True"
    run.verdict = Verdict(VerificationStatus.UNKNOWN, "prover", "")
    run.telemetry = Telemetry(model_calls=3, input_tokens=input_tokens,
                              output_tokens=output_tokens)
    return run


def test_the_tokens_reach_the_results_record():
    goal = Goal(id="g1", goal="g", area="a", tier=Tier.IN_MATHLIB)

    record = result_from(goal, _run_with(1234, 56))

    assert record.input_tokens == 1234
    assert record.output_tokens == 56


def test_the_summary_totals_rather_than_averages_them():
    """The bill is a total. A mean would hide that one hard goal can cost more
    than the other four together -- which is exactly the shape of the measured
    run."""
    results = [
        ProofResult(goal_id="a", area="x", tier=Tier.IN_MATHLIB,
                    outcome=_proved(), input_tokens=100, output_tokens=10),
        ProofResult(goal_id="b", area="x", tier=Tier.IN_MATHLIB,
                    outcome=_proved(), input_tokens=900, output_tokens=90),
    ]

    summary = summarize(results)

    assert summary["input_tokens"] == 1000
    assert summary["output_tokens"] == 100


def test_nothing_reported_is_none_rather_than_zero():
    """0 would say the run was free. A provider that returns no usage metadata
    has said nothing about cost, not that there was none."""
    results = [ProofResult(goal_id="a", area="x", tier=Tier.IN_MATHLIB,
                           outcome=_proved())]

    summary = summarize(results)

    assert summary["input_tokens"] is None
    assert summary["output_tokens"] is None
    assert "not reported" in render(summary)


def test_the_report_shows_the_totals_when_they_exist():
    results = [ProofResult(goal_id="a", area="x", tier=Tier.IN_MATHLIB,
                           outcome=_proved(), input_tokens=1234567,
                           output_tokens=89)]

    text = render(summarize(results))

    assert "1,234,567" in text
    assert "input tokens" in text


def test_telemetry_summary_omits_tokens_when_unreported():
    """The one-line summary is read by a human beside the baseline prover,
    which reports no tokens at all; printing "0 in / 0 out" there would be a
    claim, not a blank."""
    assert "tokens" not in Telemetry(model_calls=2).summary()
    assert "tokens" in Telemetry(model_calls=2, input_tokens=5).summary()


def _proved():
    from eval.proof_metrics import ProofOutcome

    return ProofOutcome.PROVED


# ================================================================
# A run that did not finish reports UNKNOWN cost, never zero cost
# ================================================================
# MEASURED on eval/results/putnam-run3.json. Three of five goals recorded
# `model_calls: 0` and zero tokens while having made 1, 4 and 6 Lean calls --
# they timed out or crashed, so the agent never returned a transcript to
# count. The summary then added the two survivors and presented 1,229,551
# input tokens as the cost of five goals.
#
# Two of those three also recorded the trace line "agent failed: " with
# NOTHING after the colon, because `str(exc)` is empty for plenty of real
# exceptions. The run died twice and the record could not say what killed it.
def test_a_run_with_no_transcript_is_marked_incomplete():
    """`complete=False` is the difference between "this cost nothing" and
    "nobody counted". Only the harness's failure paths set it."""
    assert Telemetry(model_calls=3, input_tokens=100).complete is True
    assert "cost UNKNOWN" in Telemetry(complete=False).summary()
    assert "cost UNKNOWN" not in Telemetry(input_tokens=5).summary()


def test_the_summary_says_how_many_goals_the_cost_covers():
    """A total is only a total if you know what it is over. Two goals'
    tokens presented as five goals' cost is not a measurement."""
    from eval.proof_metrics import ProofOutcome

    measured = ProofResult(goal_id="a", area="x", tier=Tier.IN_MATHLIB,
                           outcome=ProofOutcome.PROVED, input_tokens=1000,
                           cost_complete=True)
    lost = ProofResult(goal_id="b", area="x", tier=Tier.IN_MATHLIB,
                       outcome=ProofOutcome.EXHAUSTED, cost_complete=False)

    summary = summarize([measured, lost])

    assert summary["cost_measured_on"] == 1
    assert summary["attempted"] == 2
    assert "FLOOR" in render(summary), (
        "a partial total must be labelled as a floor, not printed as a total"
    )


def test_a_complete_run_is_not_labelled_a_floor():
    """The negative control: the warning must not appear on a clean run, or it
    becomes noise nobody reads."""
    from eval.proof_metrics import ProofOutcome

    record = ProofResult(goal_id="a", area="x", tier=Tier.IN_MATHLIB,
                         outcome=ProofOutcome.PROVED, input_tokens=1000)

    assert "FLOOR" not in render(summarize([record]))


def test_a_crash_records_the_exception_type(tmp_path, monkeypatch):
    """MEASURED: "agent failed: " with nothing after it, twice in one run.
    `str(exc)` is empty for many real exceptions, so the class name is what
    separates a rate limit from a decode error from a cancelled task."""
    from math_v2 import harness
    from math_v2.core import log

    class Boom(Exception):
        pass

    def explode(*args, **kwargs):
        raise Boom()          # deliberately empty message, as observed

    monkeypatch.setattr(harness, "_invoke", explode)
    run = harness.prove("g", model=object(), workdir=str(tmp_path),
                        agent_factory=lambda *a: object())

    trace = " ".join(log.read(str(tmp_path))["trace"])
    assert "agent failed: Boom" in trace, trace
    assert run.telemetry.complete is False, "a crashed run claimed a real cost"
