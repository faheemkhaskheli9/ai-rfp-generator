"""Tests for the outline review/approve workflow (issue #4).

Covers the state machine described in the issue's acceptance criteria:
edits are applied via PATCH and persisted, the original LLM-generated
outline is never lost (captured in an ``OutlineRevision``), approval is an
explicit, separately-recorded action, and anything downstream (Phase 2
drafting) cannot proceed past an outline that hasn't been approved.
"""

from __future__ import annotations

import json

import pytest

import ai_rfp_generator.app as app_module
from ai_rfp_generator.db import Outline, OutlineRevision, Requirement
from ai_rfp_generator.outline import (
    OutlineNotApprovedError,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_REJECTED,
    approve_outline,
    reject_outline,
    require_outline_approved,
)


class _FakeClient:
    def __init__(self, api_key, *, model=None, client=None):
        self.model_name_value = model or "fake-outline-model"

    @property
    def model_name(self) -> str:
        return self.model_name_value

    def generate(self, requirement_text: str):
        return [
            {"title": "Security", "description": "Describe SSO support."},
            {"title": "Pricing", "description": "Describe pricing tiers."},
        ]


def _create_outline(client, monkeypatch) -> int:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(app_module, "OpenAIOutlineClient", _FakeClient)

    submission = client.post("/requirements", data={"text": "Must support SSO"})
    requirement_id = submission.json()["id"]
    outline_response = client.post(f"/requirements/{requirement_id}/outline")
    return outline_response.json()["id"]


def test_new_outline_starts_in_draft_status(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)

    response = client.get(f"/outlines/{outline_id}")
    assert response.status_code == 200
    assert response.json()["status"] == STATUS_DRAFT
    assert response.json()["updated_at"] is None


def test_edit_outline_reorders_renames_and_persists(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)

    response = client.patch(
        f"/outlines/{outline_id}",
        json={
            "sections": [
                {"title": "Pricing", "description": "Describe pricing tiers in detail."},
                {"title": "Security & Compliance", "description": "Describe SSO support."},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [s["title"] for s in body["sections"]] == ["Pricing", "Security & Compliance"]
    assert [s["position"] for s in body["sections"]] == [0, 1]
    assert body["updated_at"] is not None
    # Editing a still-draft outline doesn't change its status.
    assert body["status"] == STATUS_DRAFT

    # Persisted, not just returned in the response.
    refetched = client.get(f"/outlines/{outline_id}").json()
    assert [s["title"] for s in refetched["sections"]] == ["Pricing", "Security & Compliance"]


def test_edit_outline_can_add_and_remove_sections(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)

    response = client.patch(
        f"/outlines/{outline_id}",
        json={"sections": [{"title": "Only Section", "description": "Just one now."}]},
    )
    assert response.status_code == 200
    assert len(response.json()["sections"]) == 1

    response = client.patch(
        f"/outlines/{outline_id}",
        json={
            "sections": [
                {"title": "A", "description": "First."},
                {"title": "B", "description": "Second."},
                {"title": "C", "description": "Third."},
            ]
        },
    )
    assert response.status_code == 200
    assert len(response.json()["sections"]) == 3


def test_edit_with_empty_sections_is_rejected(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)

    response = client.patch(f"/outlines/{outline_id}", json={"sections": []})
    assert response.status_code == 422

    # Original sections are untouched by the rejected edit.
    refetched = client.get(f"/outlines/{outline_id}").json()
    assert len(refetched["sections"]) == 2


def test_edit_with_blank_title_is_rejected(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)

    response = client.patch(
        f"/outlines/{outline_id}",
        json={"sections": [{"title": "   ", "description": "Something."}]},
    )
    assert response.status_code == 422


def test_edit_missing_outline_is_404(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-test")
    response = client.patch(
        "/outlines/999", json={"sections": [{"title": "A", "description": "B"}]}
    )
    assert response.status_code == 404


def test_edit_preserves_original_outline_as_a_revision(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)

    client.patch(
        f"/outlines/{outline_id}",
        json={"sections": [{"title": "Renamed", "description": "Changed."}]},
    )

    with app_module._SessionFactory() as session:
        revisions = (
            session.query(OutlineRevision)
            .filter(OutlineRevision.outline_id == outline_id)
            .all()
        )
        assert len(revisions) == 1
        snapshot = json.loads(revisions[0].sections_json)
        # The pre-edit (original LLM-generated) sections are recoverable.
        assert [s["title"] for s in snapshot] == ["Security", "Pricing"]


def test_approve_outline_sets_status_and_reviewed_at(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)

    response = client.post(f"/outlines/{outline_id}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == STATUS_APPROVED

    with app_module._SessionFactory() as session:
        outline = session.get(Outline, outline_id)
        assert outline.status == STATUS_APPROVED
        assert outline.reviewed_at is not None


def test_reject_outline_sets_status_rejected(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)

    response = client.post(f"/outlines/{outline_id}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == STATUS_REJECTED


def test_editing_an_approved_outline_resets_it_to_draft(client, monkeypatch):
    outline_id = _create_outline(client, monkeypatch)
    client.post(f"/outlines/{outline_id}/approve")

    response = client.patch(
        f"/outlines/{outline_id}",
        json={"sections": [{"title": "Changed after approval", "description": "New text."}]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == STATUS_DRAFT

    with app_module._SessionFactory() as session:
        outline = session.get(Outline, outline_id)
        assert outline.status == STATUS_DRAFT
        assert outline.reviewed_at is None


def test_approve_missing_outline_is_404(client, monkeypatch):
    response = client.post("/outlines/999/approve")
    assert response.status_code == 404


# --- Direct state-machine tests: the gate a future Phase 2 drafting step
# would call before it may proceed, exercised without going through the API.


def _bare_outline(status: str) -> Outline:
    outline = Outline(id=1, requirement_id=1, model="fake", status=status)
    return outline


def test_require_outline_approved_raises_for_draft():
    with pytest.raises(OutlineNotApprovedError):
        require_outline_approved(_bare_outline(STATUS_DRAFT))


def test_require_outline_approved_raises_for_rejected():
    outline = _bare_outline(STATUS_DRAFT)
    reject_outline(outline)
    with pytest.raises(OutlineNotApprovedError):
        require_outline_approved(outline)


def test_require_outline_approved_passes_once_approved():
    outline = _bare_outline(STATUS_DRAFT)
    approve_outline(outline)
    require_outline_approved(outline)  # does not raise
