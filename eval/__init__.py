"""Evaluation — benchmarking and regression testing (Design Doc section 9)."""

from eval.dataset import GoldenCase, load_cases
from eval.metrics import CaseResult, Outcome, classify, summarize
from eval.runner import run_all, run_case, save

__all__ = [
    "GoldenCase",
    "load_cases",
    "CaseResult",
    "Outcome",
    "classify",
    "summarize",
    "run_all",
    "run_case",
    "save",
]
