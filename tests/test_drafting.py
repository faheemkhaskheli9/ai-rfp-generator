"""Tests for grounded, cited section drafting."""

from __future__ import annotations

import json

import pytest

from ai_rfp_generator.db import (
    Fact,
    Outline,
    OutlineSection,
    Requirement,
    SourceMaterial,
    make_engine,
    make_session_factory,
    now_utc,
)
from ai_rfp_generator.drafting import (
    SectionDraftingError,
    generate_section_draft,
    persist_section_draft,
    retrieve_relevant_facts,
)
from ai_rfp_generator.outline import OutlineNotApprovedError


class FakeDraftClient:
    model_name = "fake-model"

    def __init__(self, content: str):
        self.content = content
        self.seen_fact_ids: list[int] = []

    def generate(
        self,
        *,
        section_title: str,
        section_description: str,
        facts: list[Fact],
        strategy: str,
    ) -> str:
        self.seen_fact_ids = [f.id for f in facts]
        self.strategy = strategy
        return self.content


@pytest.fixture
def drafting_case(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'drafting.db'}")
    factory = make_session_factory(engine)
    with factory() as session:
        requirement = Requirement(
            source_filename="rfp.txt",
            submitted_at=now_utc(),
            status="parsed",
            content="Describe cloud security and encryption.",
        )
        session.add(requirement)
        session.flush()

        outline = Outline(
            requirement_id=requirement.id,
            model="fake-outline",
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
            description="Describe encryption and cloud security controls.",
        )
        session.add(section)

        source = SourceMaterial(
            requirement_id=requirement.id,
            original_filename="security.txt",
            stored_path="/tmp/security.txt",
            content_hash="security-hash",
            extension=".txt",
            size_bytes=100,
            extracted_text="Data is encrypted at rest. Support is available 24/7.",
            uploaded_at=now_utc(),
        )
        session.add(source)
        session.flush()

        encryption = Fact(
            requirement_id=requirement.id,
            source_material_id=source.id,
            text="Data is encrypted at rest using managed encryption controls.",
            normalized_text="data is encrypted at rest using managed encryption controls.",
            start_offset=0,
            end_offset=58,
            is_duplicate=False,
            duplicate_of_id=None,
            extracted_at=now_utc(),
        )
        support = Fact(
            requirement_id=requirement.id,
            source_material_id=source.id,
            text="Customer support is available twenty four hours a day.",
            normalized_text="customer support is available twenty four hours a day.",
            start_offset=59,
            end_offset=110,
            is_duplicate=False,
            duplicate_of_id=None,
            extracted_at=now_utc(),
        )
        session.add_all([encryption, support])
        session.commit()
        session.refresh(section)
        session.refresh(encryption)
        session.refresh(support)
        yield session, requirement, outline, section, encryption, support


def test_retrieval_prefers_fact_matching_section(drafting_case):
    _, _, _, section, encryption, support = drafting_case

    ranked = retrieve_relevant_facts(section, [support, encryption], limit=1)

    assert [f.id for f in ranked] == [encryption.id]


def test_generate_section_requires_approved_outline(drafting_case):
    session, _, outline, section, encryption, _ = drafting_case
    outline.status = "draft"
    session.flush()

    with pytest.raises(OutlineNotApprovedError):
        generate_section_draft(FakeDraftClient(f"Encrypted [F{encryption.id}]"), section, [encryption])


def test_generated_section_requires_resolvable_citation(drafting_case):
    _, _, _, section, encryption, _ = drafting_case

    with pytest.raises(SectionDraftingError, match="not provided"):
        generate_section_draft(FakeDraftClient("Encrypted [F99999]"), section, [encryption])


def test_generated_section_requires_a_citation(drafting_case):
    _, _, _, section, encryption, _ = drafting_case

    with pytest.raises(SectionDraftingError, match="no fact citations"):
        generate_section_draft(FakeDraftClient("Data is encrypted at rest."), section, [encryption])


def test_drafts_are_persisted_as_immutable_versions(drafting_case):
    session, requirement, _, section, encryption, _ = drafting_case
    client = FakeDraftClient(f"Data is encrypted at rest [F{encryption.id}].")

    first = generate_section_draft(client, section, [encryption])
    draft_a = persist_section_draft(session, section, first)
    session.commit()

    second = generate_section_draft(client, section, [encryption])
    draft_b = persist_section_draft(session, section, second)
    session.commit()

    assert draft_a.id != draft_b.id
    assert draft_a.requirement_id == requirement.id
    assert draft_a.outline_section_id == section.id
    assert draft_a.model == "fake-model"
    assert json.loads(draft_a.fact_ids_json) == [encryption.id]
    assert len(section.drafts) == 2


def test_generation_strategy_is_selectable_and_persisted(drafting_case):
    session, _, _, section, encryption, _ = drafting_case
    client = FakeDraftClient(f"Encrypted at rest [F{encryption.id}].")

    result = generate_section_draft(
        client,
        section,
        [encryption],
        strategy="concise",
    )
    draft = persist_section_draft(session, section, result, strategy="concise")
    session.commit()

    assert client.strategy == "concise"
    assert draft.strategy == "concise"


def test_unknown_generation_strategy_is_rejected(drafting_case):
    _, _, _, section, encryption, _ = drafting_case

    with pytest.raises(SectionDraftingError, match="unknown drafting strategy"):
        generate_section_draft(
            FakeDraftClient(f"Encrypted [F{encryption.id}]"),
            section,
            [encryption],
            strategy="unsupported",
        )
