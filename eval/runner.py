"""Run the agent over the golden dataset and score it.

Two robustness rules, both learned the hard way:
  * one case failing must never stop the run — exceptions become ERROR
  * rate limits are not failures — back off and try again
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pipeline
from eval.dataset import GoldenCase, load_cases
from eval.metrics import CaseResult, Outcome, classify, summarize

RESULTS_PATH = Path(__file__).with_name("last_run.json")

# Provider quota errors. Retrying helps; retrying a bad expression does not.
RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "rate limit", "quota")


def _is_rate_limited(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker.lower() in text for marker in RATE_LIMIT_MARKERS)


def run_case(case: GoldenCase, model=None, retries: int = 3, backoff: float = 20.0):
    """Run one golden case and score it, backing off on rate limits."""
    error: Exception | None = None

    for attempt in range(retries):
        try:
            state = pipeline.run(case.question, model=model)
            return CaseResult(
                case_id=case.id,
                area=case.area,
                expected=case.expected.value,
                actual=state.verdict.status.value,
                outcome=classify(case.expected, state.verdict.status),
                checks=len(state.checks),
                attempts=len(state.attempts),
                evidence=len(state.evidence),
                tools_used=",".join(sorted({c.tool for c in state.checks})),
                detail=state.verdict.detail[:200],
            )
        except Exception as exc:  # noqa: BLE001 — one bad case must not stop the run
            error = exc
            if not _is_rate_limited(exc) or attempt == retries - 1:
                break
            wait = backoff * (attempt + 1)
            print(f"      rate limited, waiting {wait:.0f}s ...")
            time.sleep(wait)

    return CaseResult(
        case_id=case.id,
        area=case.area,
        expected=case.expected.value,
        actual="error",
        outcome=Outcome.ERROR,
        detail=str(error)[:200],
    )


def run_all(
    cases: list[GoldenCase] | None = None,
    model=None,
    pause: float = 0.0,
    verbose: bool = True,
) -> tuple[list[CaseResult], dict]:
    """Run every case, print progress, and return results plus a summary."""
    cases = cases if cases is not None else load_cases()
    results: list[CaseResult] = []
    consecutive_errors = 0

    for index, case in enumerate(cases, start=1):
        result = run_case(case, model=model)
        results.append(result)

        # A per-call backoff cannot recover from an exhausted daily quota.
        # Grinding through 80 more cases at a minute each helps nobody.
        consecutive_errors = consecutive_errors + 1 if result.outcome is Outcome.ERROR else 0
        if consecutive_errors >= 3:
            print(
                f"\nAborting: {consecutive_errors} consecutive failures. "
                "The quota is likely exhausted, not momentarily rate limited."
            )
            print("Switch model (e.g. ollama:qwen2.5:3b) or try again later.")
            print(f"Scored {len(results)} of {len(cases)} case(s).\n")
            break
        if verbose:
            mark = {
                Outcome.CORRECT: "ok  ",
                Outcome.MISSED: "miss",
                Outcome.WRONG: "WRONG",
                Outcome.ERROR: "ERR ",
            }[result.outcome]
            print(
                f"[{index:>2}/{len(cases)}] {mark}  {case.id:<28} "
                f"expected={result.expected:<5} got={result.actual:<5} "
                f"checks={result.checks}"
            )
        if pause:
            time.sleep(pause)

    return results, summarize(results)


def save(results: list[CaseResult], summary: dict, path: Path | None = None) -> Path:
    """Write results to disk so runs can be compared over time."""
    path = path or RESULTS_PATH
    payload = {
        "summary": summary,
        "results": [vars(r) | {"outcome": r.outcome.value} for r in results],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path
