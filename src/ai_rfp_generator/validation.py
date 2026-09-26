"""Deterministic validation for generated section drafts.

Validation intentionally does not use an LLM. It verifies citation syntax and
traceability against persisted facts, and flags sentence-level claims that do
not carry a resolvable citation.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ai_rfp_generator.db import DraftSection, Fact, ValidationFinding

_CITATION_RE = re.compile(r"\[F(\d+)\]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def split_claims(content: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.split(content) if part.strip()]


def validate_draft(draft: DraftSection, facts: list[Fact]) -> list[ValidationFinding]:
    """Return new validation findings for one draft.

    A sentence passes when it contains at least one citation and every cited
    fact resolves to a fact belonging to the same requirement.
    """
    allowed = {fact.id for fact in facts if fact.requirement_id == draft.requirement_id}
    findings: list[ValidationFinding] = []

    for claim in split_claims(draft.content):
        cited_ids = {int(value) for value in _CITATION_RE.findall(claim)}

        if not cited_ids:
            findings.append(
                ValidationFinding(
                    draft_section_id=draft.id,
                    finding_type="uncited_claim",
                    offending_text=claim,
                    detail="Claim has no source citation.",
                    created_at=datetime.now(timezone.utc),
                )
            )
            continue

        invalid = cited_ids - allowed
        if invalid:
            findings.append(
                ValidationFinding(
                    draft_section_id=draft.id,
                    finding_type="invalid_citation",
                    offending_text=claim,
                    detail=f"Citation references unknown facts: {sorted(invalid)}",
                    created_at=datetime.now(timezone.utc),
                )
            )

    return findings


def replace_validation_findings(session, draft: DraftSection, facts: list[Fact]) -> list[ValidationFinding]:
    """Re-run validation and replace stale findings for this immutable draft."""
    for finding in list(draft.validation_findings):
        session.delete(finding)
    session.flush()

    findings = validate_draft(draft, facts)
    session.add_all(findings)
    return findings
