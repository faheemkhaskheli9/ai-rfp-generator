"""Deterministic rubric evaluation for generated RFP sections."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ai_rfp_generator.db import DraftSection, EvaluationScore, Fact
from ai_rfp_generator.validation import split_claims

_CITATION_RE = re.compile(r"\[F(\d+)\]")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_RISKY_TONE = (
    "guarantee",
    "guaranteed",
    "100% secure",
    "zero downtime",
    "best in class",
    "unmatched",
    "never fails",
)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text) if len(token) > 3}


def grounding_score(draft: DraftSection, facts: list[Fact]) -> tuple[int, str]:
    claims = split_claims(draft.content)
    if not claims:
        return 0, "Draft is empty."

    valid_fact_ids = {fact.id for fact in facts if fact.requirement_id == draft.requirement_id}
    supported = 0
    for claim in claims:
        citations = {int(value) for value in _CITATION_RE.findall(claim)}
        if citations and citations <= valid_fact_ids:
            supported += 1

    score = round(100 * supported / len(claims))
    return score, f"{supported}/{len(claims)} claim sentences have resolvable citations."


def completeness_score(draft: DraftSection) -> tuple[int, str]:
    expected = _tokens(
        f"{draft.outline_section.title} {draft.outline_section.description}"
    )
    actual = _tokens(draft.content)
    if not expected:
        return 100, "No meaningful outline keywords to evaluate."
    covered = expected & actual
    score = round(100 * len(covered) / len(expected))
    return score, f"{len(covered)}/{len(expected)} outline keywords appear in the draft."


def tone_score(draft: DraftSection) -> tuple[int, str]:
    lowered = draft.content.lower()
    matches = [phrase for phrase in _RISKY_TONE if phrase in lowered]
    score = max(0, 100 - 20 * len(matches))
    if matches:
        return score, "Risky or absolute wording detected: " + ", ".join(matches)
    return score, "No configured risky or absolute wording detected."


def evaluate_draft(draft: DraftSection, facts: list[Fact]) -> list[EvaluationScore]:
    """Calculate the current deterministic rubric for a draft."""
    criteria = {
        "grounding": grounding_score(draft, facts),
        "completeness": completeness_score(draft),
        "tone": tone_score(draft),
    }
    now = datetime.now(timezone.utc)
    return [
        EvaluationScore(
            draft_section_id=draft.id,
            criterion=criterion,
            score=score,
            detail=detail,
            created_at=now,
        )
        for criterion, (score, detail) in criteria.items()
    ]


def replace_evaluation_scores(session, draft: DraftSection, facts: list[Fact]) -> list[EvaluationScore]:
    for score in list(draft.evaluation_scores):
        session.delete(score)
    session.flush()

    scores = evaluate_draft(draft, facts)
    session.add_all(scores)
    return scores
