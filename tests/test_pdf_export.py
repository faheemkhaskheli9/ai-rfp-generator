"""Tests for finalized PDF export (issue #14)."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from ai_rfp_generator.db import DraftSection, Outline, OutlineSection, Requirement, now_utc


def _seed_pdf_case(app_module):
    with app_module._SessionFactory() as session:
        requirement = Requirement(
            source_filename="rfp.txt",
            submitted_at=now_utc(),
            status="parsed",
            content="Describe solution and implementation.",
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

        first = OutlineSection(
            outline_id=outline.id,
            position=0,
            title="Solution Overview",
            description="Describe the solution.",
        )
        second = OutlineSection(
            outline_id=outline.id,
            position=1,
            title="Implementation",
            description="Describe implementation.",
        )
        session.add_all([first, second])
        session.flush()

        session.add_all(
            [
                DraftSection(
                    requirement_id=requirement.id,
                    outline_section_id=first.id,
                    strategy="concise",
                    model="test",
                    content="The proposed solution addresses the requested scope.",
                    fact_ids_json="[]",
                    generated_at=now_utc(),
                    status="approved",
                    reviewed_at=now_utc(),
                ),
                DraftSection(
                    requirement_id=requirement.id,
                    outline_section_id=second.id,
                    strategy="detailed",
                    model="test",
                    content="Implementation is described in phased delivery steps.",
                    fact_ids_json="[]",
                    generated_at=now_utc(),
                    status="approved",
                    reviewed_at=now_utc(),
                ),
            ]
        )
        session.commit()
        return outline.id


def test_pdf_export_is_well_formed_and_preserves_section_order(client):
    import ai_rfp_generator.app as app_module

    outline_id = _seed_pdf_case(app_module)

    response = client.post(f"/outlines/{outline_id}/export?format=pdf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")

    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) >= 1
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert "RFP Response" in text
    assert "Table of Contents" in text
    assert "1. Solution Overview" in text
    assert "2. Implementation" in text
    assert "Solution Overview" in text
    assert "Implementation" in text
    assert text.index("Solution Overview") < text.index("Implementation")


def test_export_rejects_unknown_format(client):
    import ai_rfp_generator.app as app_module

    outline_id = _seed_pdf_case(app_module)

    response = client.post(f"/outlines/{outline_id}/export?format=html")

    assert response.status_code == 400
    assert "docx, pdf" in response.json()["detail"]
