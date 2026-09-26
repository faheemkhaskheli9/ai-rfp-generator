"""Tests for draft review and approval workflow."""

from __future__ import annotations

from ai_rfp_generator.db import DraftSection, Outline, OutlineSection, Requirement, now_utc


def _seed_versions(app_module):
    with app_module._SessionFactory() as session:
        requirement = Requirement(
            source_filename="rfp.txt",
            submitted_at=now_utc(),
            status="parsed",
            content="Describe the solution.",
        )
        session.add(requirement)
        session.flush()

        outline = Outline(
            requirement_id=requirement.id,
            model="test",
            generated_at=now_utc(),
            status="approved",
            reviewed_at=now_utc(),
        )
        session.add(outline)
        session.flush()

        section = OutlineSection(
            outline_id=outline.id,
            position=0,
            title="Solution",
            description="Describe the solution.",
        )
        session.add(section)
        session.flush()

        first = DraftSection(
            requirement_id=requirement.id,
            outline_section_id=section.id,
            strategy="concise",
            model="test",
            content="Version one.",
            fact_ids_json="[]",
            generated_at=now_utc(),
            status="draft",
        )
        second = DraftSection(
            requirement_id=requirement.id,
            outline_section_id=section.id,
            strategy="detailed",
            model="test",
            content="Version two.",
            fact_ids_json="[]",
            generated_at=now_utc(),
            status="draft",
        )
        session.add_all([first, second])
        session.commit()
        return first.id, second.id


def test_only_one_draft_version_can_be_approved(client):
    import ai_rfp_generator.app as app_module

    first_id, second_id = _seed_versions(app_module)

    first = client.post(f"/draft-sections/{first_id}/approve")
    assert first.status_code == 200
    assert first.json()["status"] == "approved"

    second = client.post(f"/draft-sections/{second_id}/approve")
    assert second.status_code == 200
    assert second.json()["status"] == "approved"

    with app_module._SessionFactory() as session:
        first_db = session.get(DraftSection, first_id)
        second_db = session.get(DraftSection, second_id)
        assert first_db.status == "draft"
        assert second_db.status == "approved"


def test_rejected_draft_is_not_approved(client):
    import ai_rfp_generator.app as app_module

    first_id, _ = _seed_versions(app_module)
    response = client.post(f"/draft-sections/{first_id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["reviewed_at"] is not None
