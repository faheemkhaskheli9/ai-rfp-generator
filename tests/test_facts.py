"""Tests for cited fact extraction from source materials (issue #6).

No LLM/network calls — extraction is deterministic sentence-boundary
splitting over ``SourceMaterial.extracted_text`` (see ``facts.py`` module
docstring for why this is the right call for a *citable* fact record, unlike
``outline.py``'s LLM-based generation).
"""

from __future__ import annotations

import pytest

from ai_rfp_generator.db import Fact, Requirement, SourceMaterial, make_engine, make_session_factory, now_utc
from ai_rfp_generator.facts import (
    extract_and_persist_facts,
    extract_fact_candidates,
    find_duplicate,
    normalize_fact_text,
)


@pytest.fixture
def session_and_requirement(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'unit.db'}")
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        requirement = Requirement(
            source_filename=None, submitted_at=now_utc(), status="received", content="x"
        )
        session.add(requirement)
        session.commit()
        session.refresh(requirement)
        yield session, requirement


def _add_source_material(session, requirement, text: str, filename: str = "past_proposal.txt") -> SourceMaterial:
    source_material = SourceMaterial(
        requirement_id=requirement.id,
        original_filename=filename,
        stored_path=f"/tmp/{filename}",
        content_hash=f"hash-{filename}",
        extension=".txt",
        size_bytes=len(text),
        extracted_text=text,
        uploaded_at=now_utc(),
    )
    session.add(source_material)
    session.commit()
    session.refresh(source_material)
    return source_material


# ---------------------------------------------------------------------------
# Candidate extraction + offsets resolve back to the right passage
# ---------------------------------------------------------------------------


def test_extract_fact_candidates_offsets_resolve_to_original_text():
    text = (
        "Acme Robotics delivered a warehouse automation platform in 2023. "
        "The platform processed over one million orders in its first year."
    )
    source_material = SourceMaterial(
        requirement_id=1,
        original_filename="case_study.txt",
        stored_path="/tmp/case_study.txt",
        content_hash="abc",
        extension=".txt",
        size_bytes=len(text),
        extracted_text=text,
        uploaded_at=now_utc(),
    )

    candidates = extract_fact_candidates(source_material)

    assert len(candidates) == 2
    for candidate in candidates:
        # The citation offsets must point at the *exact* substring the fact
        # text came from -- not merely somewhere similar.
        assert text[candidate.start_offset : candidate.end_offset] == candidate.text
    assert candidates[0].text == "Acme Robotics delivered a warehouse automation platform in 2023."
    assert candidates[1].text == "The platform processed over one million orders in its first year."


def test_extract_fact_candidates_drops_short_fragments():
    source_material = SourceMaterial(
        requirement_id=1,
        original_filename="notes.txt",
        stored_path="/tmp/notes.txt",
        content_hash="abc",
        extension=".txt",
        size_bytes=10,
        extracted_text="Overview.\nWe delivered a complete customer portal for a regional retailer.",
        uploaded_at=now_utc(),
    )

    candidates = extract_fact_candidates(source_material)

    assert all(len(c.text) >= 15 for c in candidates)
    assert "We delivered a complete customer portal for a regional retailer." in [c.text for c in candidates]


def test_fact_extracted_from_sample_document_carries_correct_source_reference(session_and_requirement):
    session, requirement = session_and_requirement
    text = "We delivered a claims-processing system for a mid-size insurer in 2022."
    source_material = _add_source_material(session, requirement, text)

    facts = extract_and_persist_facts(session, source_material)
    session.commit()

    assert len(facts) == 1
    fact = facts[0]
    assert fact.source_material_id == source_material.id
    assert fact.requirement_id == requirement.id
    # The citation reference (document id + offsets) must resolve back to
    # the exact passage in the *source* document's stored text.
    resolved = source_material.extracted_text[fact.start_offset : fact.end_offset]
    assert resolved == fact.text
    assert resolved == text


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------


def test_normalize_fact_text_collapses_whitespace_and_case():
    assert normalize_fact_text("  We   Delivered\nthe Portal.  ") == "we delivered the portal."


def test_near_duplicate_facts_across_documents_are_flagged_not_persisted_as_unrelated(
    session_and_requirement,
):
    """A naive implementation that never de-duplicates would persist both
    facts with is_duplicate=False, which this test would then fail.
    """
    session, requirement = session_and_requirement
    doc_a = _add_source_material(
        session, requirement, "We delivered the claims portal for the client in 2022.", "case_study_a.txt"
    )
    doc_b = _add_source_material(
        session, requirement, "We delivered the claims portal for the client back in 2022!", "case_study_b.txt"
    )

    facts_a = extract_and_persist_facts(session, doc_a)
    session.commit()
    facts_b = extract_and_persist_facts(session, doc_b)
    session.commit()

    assert len(facts_a) == 1
    assert len(facts_b) == 1
    assert facts_a[0].is_duplicate is False
    assert facts_a[0].duplicate_of_id is None

    assert facts_b[0].is_duplicate is True
    assert facts_b[0].duplicate_of_id == facts_a[0].id
    # The duplicate must still carry its OWN citation back to its own
    # document -- it is flagged, not discarded or merged away.
    assert facts_b[0].source_material_id == doc_b.id
    resolved = doc_b.extracted_text[facts_b[0].start_offset : facts_b[0].end_offset]
    assert resolved == facts_b[0].text


def test_exact_duplicate_within_same_document_is_flagged(session_and_requirement):
    session, requirement = session_and_requirement
    text = "We support 24/7 customer service. We support 24/7 customer service."
    source_material = _add_source_material(session, requirement, text)

    facts = extract_and_persist_facts(session, source_material)
    session.commit()

    assert len(facts) == 2
    assert facts[0].is_duplicate is False
    assert facts[1].is_duplicate is True
    assert facts[1].duplicate_of_id == facts[0].id
    # Distinct offsets even though the text is identical.
    assert facts[0].start_offset != facts[1].start_offset


def test_genuinely_different_facts_are_both_persisted_unflagged(session_and_requirement):
    session, requirement = session_and_requirement
    doc_a = _add_source_material(
        session, requirement, "We delivered a claims-processing system for an insurer.", "case_study_a.txt"
    )
    doc_b = _add_source_material(
        session, requirement, "Our team holds ISO 27001 certification for information security.", "case_study_b.txt"
    )

    facts_a = extract_and_persist_facts(session, doc_a)
    session.commit()
    facts_b = extract_and_persist_facts(session, doc_b)
    session.commit()

    assert facts_a[0].is_duplicate is False
    assert facts_b[0].is_duplicate is False
    assert facts_b[0].duplicate_of_id is None
    assert session.query(Fact).count() == 2


def test_duplicate_of_chain_never_nests_through_another_duplicate(session_and_requirement):
    session, requirement = session_and_requirement
    doc_a = _add_source_material(session, requirement, "We deployed the platform in Q1 2023.", "a.txt")
    doc_b = _add_source_material(session, requirement, "We deployed the platform in Q1 2023 for them.", "b.txt")
    doc_c = _add_source_material(session, requirement, "We deployed the platform in Q1 2023, yes.", "c.txt")

    fact_a = extract_and_persist_facts(session, doc_a)[0]
    session.commit()
    fact_b = extract_and_persist_facts(session, doc_b)[0]
    session.commit()
    fact_c = extract_and_persist_facts(session, doc_c)[0]
    session.commit()

    assert fact_b.duplicate_of_id == fact_a.id
    # fact_c should resolve to the root (fact_a), not chain through fact_b.
    assert fact_c.duplicate_of_id == fact_a.id


def test_find_duplicate_returns_none_for_no_existing_facts():
    assert find_duplicate("anything", []) is None
