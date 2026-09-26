"""Tests for deterministic rubric evaluation (issue #11)."""

from __future__ import annotations

import json

from ai_rfp_generator.db import DraftSection, Fact, Outline, OutlineSection, Requirement, SourceMaterial, now_utc


def _seed(app_module, content: str):
    with app_module._SessionFactory() as session:
        requirement = Requirement(
            source_filename="rfp.txt",
            submitted_at=now_utc(),
            status="parsed",
            content="Describe cloud security and encryption controls.",
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
            title="Cloud Security",
            description="Describe encryption controls for customer data.",
        )
        session.add(section)
        session.flush()

        source = SourceMaterial(
            requirement_id=requirement.id,
            original_filename="security.txt",
            stored_path="/tmp/security.txt",
            content_hash=f"eval-{requirement.id}",
            extension=".txt",
            size_bytes=30,
            extracted_text="Customer data is encrypted at rest.",
            uploaded_at=now_utc(),
        )
        session.add(source)
        session.flush()

        fact = Fact(
            requirement_id=requirement.id,
            source_material_id=source.id,
            text="Customer data is encrypted at rest.",
            normalized_text="customer data is encrypted at rest.",
            start_offset=0,
            end_offset=35,
            is_duplicate=False,
            duplicate_of_id=None,
            extracted_at=now_utc(),
        )
        session.add(fact)
        session.flush()

        draft = DraftSection(
            requirement_id=requirement.id,
            outline_section_id=section.id,
            strategy="concise",
            model="test-model",
            content=content.format(fact_id=fact.id),
            fact_ids_json=json.dumps([fact.id]),
            generated_at=now_utc(),
        )
        session.add(draft)
        session.commit()
        return draft.id


def test_evaluation_persists_three_scores(client):
    import ai_rfp_generator.app as app_module

    draft_id = _seed(
        app_module,
        "Cloud security protects customer data with encryption controls [F{fact_id}].",
    )

    response = client.post(f"/draft-sections/{draft_id}/evaluate")

    assert response.status_code == 200
    payload = response.json()
    scores = {item["criterion"]: item["score"] for item in payload["scores"]}
    assert set(scores) == {"grounding", "completeness", "tone"}
    assert all(0 <= score <= 100 for score in scores.values())
    assert scores["grounding"] == 100
    assert scores["tone"] == 100

    stored = client.get(f"/draft-sections/{draft_id}/evaluation")
    assert stored.status_code == 200
    assert len(stored.json()["scores"]) == 3


def test_uncited_and_risky_text_lowers_scores(client):
    import ai_rfp_generator.app as app_module

    draft_id = _seed(
        app_module,
        "Customer data is encrypted at rest [F{fact_id}]. We guarantee zero downtime.",
    )

    payload = client.post(f"/draft-sections/{draft_id}/evaluate").json()
    scores = {item["criterion"]: item["score"] for item in payload["scores"]}

    assert scores["grounding"] == 50
    assert scores["tone"] < 100


def test_evaluation_rerun_replaces_scores(client):
    import ai_rfp_generator.app as app_module

    draft_id = _seed(
        app_module,
        "Customer data is encrypted at rest [F{fact_id}].",
    )

    first = client.post(f"/draft-sections/{draft_id}/evaluate")
    second = client.post(f"/draft-sections/{draft_id}/evaluate")

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(second.json()["scores"]) == 3
