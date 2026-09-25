"""Same question, same answer.

Variance that is a measurement problem for a benchmark is a TRUST problem
for a user: ask twice, get two answers, from a tool whose whole claim is
that it does not guess.

Most of these tests are about what the cache REFUSES to do. A cache that
can upgrade a verdict, outlive its configuration, or freeze a crash has
made the system worse than having no cache at all.
"""

import json

import pytest

from domain.verdict import Verdict, VerificationStatus
from pipeline import cache


@pytest.fixture
def caching(tmp_path, monkeypatch):
    monkeypatch.setenv("MRA_CACHE", str(tmp_path))
    return tmp_path


class Run:
    def __init__(self, answer="[VERIFIED TRUE] because", status=VerificationStatus.TRUE,
                 method="sympy"):
        self.answer = answer
        self.verdict = (Verdict(status, method, "detail") if status else None)


# ------------------------------------------------------------- the switch
def test_it_is_off_unless_a_directory_is_given(monkeypatch):
    """Every switch in this project stays measurable against the thing it
    replaces, and this one changes what the system returns."""
    monkeypatch.delenv("MRA_CACHE", raising=False)
    assert not cache.enabled()
    assert cache.load("anything") is None
    assert cache.store("anything", Run()) is False


def test_a_stored_answer_comes_back_verbatim(caching):
    assert cache.store("is 2+2 4?", Run("[VERIFIED TRUE] yes")) is True
    found = cache.load("is 2+2 4?")
    assert found.answer == "[VERIFIED TRUE] yes"
    assert found.verdict == "true"


def test_a_question_never_asked_is_a_miss(caching):
    cache.store("one question", Run())
    assert cache.load("a different question") is None


def test_whitespace_around_a_question_does_not_make_a_new_entry(caching):
    cache.store("is 2+2 4?", Run("[VERIFIED TRUE] yes"))
    assert cache.load("  is 2+2 4?  ").answer == "[VERIFIED TRUE] yes"


# ------------------------------------------- what must never be served
def test_a_configuration_change_is_a_miss(caching, monkeypatch):
    """A cache that survived a config change would serve yesterday's answer
    for today's system and call it consistency."""
    cache.store("a question", Run("[VERIFIED TRUE] under the old model"))
    assert cache.load("a question") is not None

    import config
    monkeypatch.setattr(config, "MODEL", "some:other-model")
    assert cache.load("a question") is None


@pytest.mark.parametrize(
    "variable, value",
    [("MRA_PROVER", "something-else"), ("MRA_LEAN_BACKEND", "subprocess"),
     ("MRA_BUDGET_PROFILE", "hard-reasoning")],
)
def test_every_setting_that_changes_an_answer_is_in_the_key(caching, monkeypatch,
                                                            variable, value):
    cache.store("a question", Run())
    assert cache.load("a question") is not None
    monkeypatch.setenv(variable, value)
    assert cache.load("a question") is None


def test_a_run_that_did_not_finish_is_not_stored(caching):
    """Freezing a crash turns one bad minute into an answer the user can
    never get past. The next caller should get a fresh attempt, which is
    the behaviour without a cache at all."""
    assert cache.store("a question", Run(status=None)) is False
    assert cache.load("a question") is None


def test_an_empty_answer_is_not_stored(caching):
    assert cache.store("a question", Run(answer="   ")) is False
    assert cache.load("a question") is None


def test_the_cache_cannot_upgrade_a_verdict(caching):
    """It replays what happened. There is no path here that turns an
    unverified answer into a verified one."""
    cache.store("a question", Run("[NOT VERIFIED] reasoning only",
                                  VerificationStatus.UNKNOWN))
    found = cache.load("a question")
    assert found.verdict == "unknown"
    assert found.answer.startswith("[NOT VERIFIED]")


# ------------------------------------------------- it must not break a request
def test_a_corrupt_entry_is_a_miss_not_an_error(caching):
    """A cache that can fail a request is worse than no cache."""
    cache.store("a question", Run())
    path = next(caching.glob("*.json"))
    path.write_text("{ this is not json", encoding="utf-8")
    assert cache.load("a question") is None


def test_an_entry_missing_its_fields_is_a_miss(caching):
    cache.store("a question", Run())
    path = next(caching.glob("*.json"))
    path.write_text(json.dumps({"stored_at": "now"}), encoding="utf-8")
    assert cache.load("a question") is None


def test_a_directory_that_cannot_be_written_does_not_raise(monkeypatch, tmp_path):
    blocked = tmp_path / "a-file-not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MRA_CACHE", str(blocked / "inside"))
    # Creating the folder fails because the parent is a file.
    try:
        stored = cache.store("a question", Run())
    except Exception as exc:  # pragma: no cover - this is the thing tested
        pytest.fail(f"a cache write must not raise: {exc}")
    assert stored is False


# ------------------------------------------------------------------- the key
def test_the_key_is_stable_for_the_same_question_and_config(caching):
    assert cache.key("a question") == cache.key("a question")


def test_the_key_separates_different_questions(caching):
    assert cache.key("one") != cache.key("two")


def test_the_stored_file_records_the_configuration_it_was_made_under(caching):
    """So a stored answer can be explained later. It is not read back on
    load -- the key already decided the entry belongs to this
    configuration, and re-deriving it would be a second source of truth."""
    cache.store("a question", Run())
    data = json.loads(next(caching.glob("*.json")).read_text(encoding="utf-8"))
    assert "config" in data and "model" in data["config"]
    assert "stored_at" in data


# ================================================== wired into the pipeline
def test_the_pipeline_replays_instead_of_calling_the_model(caching, monkeypatch):
    """The point of the whole file: the second ask must not reach the model,
    and must return byte-identical text."""
    from pipeline import pipeline

    calls = []

    def fake_invoke(model, question, extra="", log=None):
        calls.append(question)
        return [], "the model's prose"

    monkeypatch.setattr(pipeline, "invoke_once", fake_invoke)
    monkeypatch.setattr(pipeline, "get_model", lambda: object())

    first = pipeline.run("is 2+2 4?")
    assert len(calls) >= 1

    before = len(calls)
    second = pipeline.run("is 2+2 4?")

    assert len(calls) == before, "the model was called again"
    assert second.answer == first.answer
    assert any("replayed" in line for line in second.trace)


def test_without_the_cache_the_model_is_asked_every_time(monkeypatch):
    from pipeline import pipeline

    monkeypatch.delenv("MRA_CACHE", raising=False)
    calls = []

    def fake_invoke(model, question, extra="", log=None):
        calls.append(question)
        return [], "prose"

    monkeypatch.setattr(pipeline, "invoke_once", fake_invoke)
    monkeypatch.setattr(pipeline, "get_model", lambda: object())

    pipeline.run("a question")
    before = len(calls)
    pipeline.run("a question")
    assert len(calls) > before
