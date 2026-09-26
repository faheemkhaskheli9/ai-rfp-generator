"""CLI for offline prompt regression cases.

Usage:
    PYTHONPATH=src python scripts/run_prompt_tests.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_rfp_generator.prompt_regression import load_cases, run_cases

DEFAULT_CASES = Path("configs/prompt_test_cases.json")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CASES
    results = run_cases(load_cases(path))

    failed = 0
    for result in results:
        state = "PASS" if result.passed else "FAIL"
        suffix = f" — {result.detail}" if result.detail else ""
        print(f"{state} {result.case_id}{suffix}")
        if not result.passed:
            failed += 1

    print(f"\n{len(results) - failed}/{len(results)} prompt regression cases passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
