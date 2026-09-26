"""Tests for the offline prompt regression framework."""

from __future__ import annotations

from pathlib import Path

from ai_rfp_generator.prompt_regression import load_cases, run_cases


def test_checked_in_prompt_regression_cases_pass():
    cases = load_cases(Path("configs/prompt_test_cases.json"))
    results = run_cases(cases)

    assert len(results) >= 3
    assert all(result.passed for result in results), results


def test_runner_reports_unknown_stage_as_failure():
    results = run_cases(
        [
            {
                "id": "bad-stage",
                "stage": "does-not-exist",
                "prompt_version": "v1",
                "input": {},
                "expect": {},
            }
        ]
    )

    assert results[0].passed is False
    assert "unknown stage" in results[0].detail
