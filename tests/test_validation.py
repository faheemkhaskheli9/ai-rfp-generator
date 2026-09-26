"""Tests for persisted citation validation (issue #9)."""

from __future__ import annotations

import json

from ai_rfp_generator.db import DraftSection, Fact, Outline, OutlineSection, Requirement, SourceMaterial, now_utc


def _seed_draft(app_module, content: str):
    with app_module._SessionFactory() as session:
        requirement = Requirement(
            source_filename="rfp.txt",
            submitted_at=now_utc(),
            status="parsed",
            content="Describe encryption controls.",
        )
        session.add(requirement)
        session.flush()

        outline = Outline(
            requirement_id=requirement.id,
            model="test-outline",
            generated_at=now_utc(),
            status="approved",
            reviewed_at=now_utc(),
        )
        session.add(outline)
        session.flush()

        section = OutlineSection(
            outline_id=outline.id,
            position=0,
            title="Security",
            description="Describe encryption controls.",
        )
        session.add(section)
        session.flush()

        source = SourceMaterial(
            requirement_id=requirement.id,
            original_filename="security.txt",
            stored_path="/tmp/security.txt",
            content_hash=f"hash-{requirement.id}",
            extension=".txt",
            size_bytes=50,
            extracted_text="Data is encrypted at rest.",
            uploaded_at=now_utc(),
        )
        session.add(source)
        session.flush()

        fact = Fact(
            requirement_id=requirement.id,
            source_material_id=source.id,
            text="Data is encrypted at rest.",
            normalized_text="data is encrypted at rest.",
            start_offset=0,
            end_offset=26,
            is_duplicate=False,
            duplicate_of_id=None,
            extracted_at=now_utc(),
        )
        session.add(fact)
        session.flush()

        rendered = content.format(fact_id=fact.id)
        draft = DraftSection(
            requirement_id=requirement.id,
            outline_section_id=section.id,
            strategy="concise",
            model="test-model",
            content=rendered,
            fact_ids_json=json.dumps([fact.id]),
            generated_at=now_utc(),
        )
        session.add(draft)
        session.commit()
        return draft.id, fact.id


def test_fully_cited_draft_has_no_findings(client):
    import ai_rfp_generator.app as app_module

    draft_id, _ = _seed_draft(
        app_module,
        "Customer data is encrypted at rest [F{fact_id}].",
    )

    response = client.post(f"/draft-sections/{draft_id}/validate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["findings"] == []

    stored = client.get(f"/draft-sections/{draft_id}/validation")
    assert stored.status_code == 200
    assert stored.json()["valid"] is True


def test_uncited_claim_is_flagged_and_persisted(client):
    import ai_rfp_generator.app as app_module

    draft_id, _ = _seed_draft(
        app_module,
        "Customer data is encrypted at rest [F{fact_id}]. We guarantee zero downtime.",
    )

    response = client.post(f"/draft-sections/{draft_id}/validate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["finding_type"] == "uncited_claim"
    assert payload["findings"][0]["offending_text"] == "We guarantee zero downtime."

    stored = client.get(f"/draft-sections/{draft_id}/validation")
    assert stored.status_code == 200
    assert stored.json()["findings"][0]["finding_type"] == "uncited_claim"


def test_unknown_fact_citation_is_flagged(client):
    import ai_rfp_generator.app as app_module

    draft_id, _ = _seed_draft(
        app_module,
        "Customer data is encrypted at rest [F999999].",
    )

    response = client.post(f"/draft-sections/{draft_id}/validate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert payload["findings"][0]["finding_type"] == "invalid_citation"


def test_validation_rerun_replaces_old_findings(client):
    import ai_rfp_generator.app as app_module

    draft_id, _ = _seed_draft(
        app_module,
        "This statement has no citation.",
    )

    first = client.post(f"/draft-sections/{draft_id}/validate")
    second = client.post(f"/draft-sections/{draft_id}/validate")

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(second.json()["findings"]) == 1
