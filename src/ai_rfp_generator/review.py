"""Human review state for immutable generated drafts."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_rfp_generator.db import DraftSection

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


class DraftReviewError(RuntimeError):
    """Invalid draft review transition."""


def approve_draft(session, draft: DraftSection) -> DraftSection:
    """Approve one draft and revoke approval from sibling versions."""
    siblings = (
        session.query(DraftSection)
        .filter(DraftSection.outline_section_id == draft.outline_section_id)
        .all()
    )
    now = datetime.now(timezone.utc)
    for sibling in siblings:
        if sibling.id == draft.id:
            sibling.status = STATUS_APPROVED
            sibling.reviewed_at = now
        elif sibling.status == STATUS_APPROVED:
            sibling.status = STATUS_DRAFT
            sibling.reviewed_at = None
    return draft


def reject_draft(draft: DraftSection) -> DraftSection:
    draft.status = STATUS_REJECTED
    draft.reviewed_at = datetime.now(timezone.utc)
    return draft


def require_approved_draft(draft: DraftSection) -> None:
    if draft.status != STATUS_APPROVED:
        raise DraftReviewError(
            f"draft {draft.id} is not approved (status={draft.status!r})"
        )
