"""Offline prompt/behavior regression runner.

Cases are JSON-defined and execute against fake model clients. This catches
schema, citation, and validation regressions in CI without network calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_rfp_generator.db import DraftSection, Fact, Outline, OutlineSection, RequirementItem, now_utc
from ai_rfp_generator.drafting import generate_section_draft
from ai_rfp_generator.outline import generate_outline
from ai_rfp_generator.validation import validate_draft


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    detail: str = ""


class _OutlineClient:
    model_name = "prompt-test-outline"

    def __init__(self, output: list[dict[str, Any]]):
        self.output = output

    def generate(self, requirement_text: str) -> list[dict[str, Any]]:
        return self.output


class _DraftClient:
    model_name = "prompt-test-drafter"

    def __init__(self, output: str):
        self.output = output

    def generate(self, *, section_title, section_description, facts, strategy):
        return self.output


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("prompt test file must contain a non-empty 'cases' list")
    return cases


def _run_outline(case: dict[str, Any]) -> None:
    data = case["input"]
    items = [
        RequirementItem(
            id=index + 1,
            requirement_id=1,
            position=index,
            item_type=item["item_type"],
            content=item["content"],
        )
        for index, item in enumerate(data["items"])
    ]
    draft = generate_outline(_OutlineClient(data["fake_output"]), items)
    expected = case["expect"]
    if len(draft.sections) < int(expected.get("min_sections", 1)):
        raise AssertionError("generated outline has too few sections")
    if expected.get("titles_nonempty") and any(not s.title.strip() for s in draft.sections):
        raise AssertionError("generated outline contains an empty title")


def _fact(fact_id: int, text: str) -> Fact:
    return Fact(
        id=fact_id,
        requirement_id=1,
        source_material_id=1,
        text=text,
        normalized_text=text.lower(),
        start_offset=0,
        end_offset=len(text),
        is_duplicate=False,
        duplicate_of_id=None,
        extracted_at=now_utc(),
    )


def _run_drafting(case: dict[str, Any]) -> None:
    data = case["input"]
    outline = Outline(
        id=1,
        requirement_id=1,
        model="prompt-test",
        generated_at=now_utc(),
        status="approved",
        reviewed_at=now_utc(),
    )
    section = OutlineSection(
        id=1,
        outline_id=1,
        position=0,
        title=data["section_title"],
        description=data["section_description"],
    )
    section.outline = outline
    facts = [_fact(i + 1, text) for i, text in enumerate(data["facts"])]
    result = generate_section_draft(
        _DraftClient(data["fake_output"]),
        section,
        facts,
        strategy="concise",
    )
    if case["expect"].get("citations_required") and "[F" not in result.content:
        raise AssertionError("generated draft does not contain a citation")


def _run_validation(case: dict[str, Any]) -> None:
    data = case["input"]
    fact = _fact(1, data["fact"])
    draft = DraftSection(
        id=1,
        requirement_id=1,
        outline_section_id=1,
        strategy="concise",
        model="prompt-test",
        content=data["draft"],
        fact_ids_json="[1]",
        generated_at=now_utc(),
    )
    findings = validate_draft(draft, [fact])
    actual = sorted(f.finding_type for f in findings)
    expected = sorted(case["expect"].get("finding_types", []))
    if actual != expected:
        raise AssertionError(f"finding types differ: expected={expected}, actual={actual}")


_STAGE_RUNNERS = {
    "outline": _run_outline,
    "drafting": _run_drafting,
    "validation": _run_validation,
}


def run_cases(cases: list[dict[str, Any]]) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        case_id = str(case.get("id", "<unnamed>"))
        stage = case.get("stage")
        runner = _STAGE_RUNNERS.get(stage)
        if runner is None:
            results.append(CaseResult(case_id, False, f"unknown stage: {stage!r}"))
            continue
        try:
            if not case.get("prompt_version"):
                raise AssertionError("prompt_version is required")
            runner(case)
        except Exception as exc:
            results.append(CaseResult(case_id, False, str(exc)))
        else:
            results.append(CaseResult(case_id, True))
    return results
