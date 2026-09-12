"""Tests for LLM-generated outline building/validation (issue #3).

No network access or API key is used here — a fake client stands in for
``OpenAIOutlineClient``, mirroring how ``OpenAIClassifierClient`` is tested
elsewhere in this portfolio (ai-email-agent).
"""

from __future__ import annotations

import pytest

from ai_rfp_generator.db import Requirement, RequirementItem, make_engine, make_session_factory, now_utc
from ai_rfp_generator.outline import OutlineGenerationError, generate_outline, persist_outline


class _FakeClient:
    def __init__(self, sections, model="fake-outline-model"):
        self._sections = sections
        self._model = model
        self.received_text: str | None = None

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, requirement_text: str):
        self.received_text = requirement_text
        return self._sections


def _items() -> list[RequirementItem]:
    return [
        RequirementItem(requirement_id=1, position=1, item_type="requirement", content="Must support SSO."),
        RequirementItem(requirement_id=1, position=0, item_type="section", content="Security:"),
    ]


def test_generate_outline_returns_ordered_sections():
    client = _FakeClient([
        {"title": "Security", "description": "Describe SSO support."},
        {"title": "Compliance", "description": "Describe certifications."},
    ])

    draft = generate_outline(client, _items())

    assert draft.model == "fake-outline-model"
    assert [s.title for s in draft.sections] == ["Security", "Compliance"]
    assert [s.position for s in draft.sections] == [0, 1]


def test_generate_outline_orders_prompt_text_by_item_position():
    client = _FakeClient([{"title": "T", "description": "D"}])
    generate_outline(client, _items())
    # position 0 ("section: Security:") must precede position 1 in the prompt
    assert client.received_text.index("Security:") < client.received_text.index("Must support SSO.")


def test_generate_outline_rejects_empty_items():
    with pytest.raises(OutlineGenerationError, match="zero requirement items"):
        generate_outline(_FakeClient([]), [])


def test_generate_outline_rejects_empty_section_list():
    with pytest.raises(OutlineGenerationError, match="no sections"):
        generate_outline(_FakeClient([]), _items())


def test_generate_outline_rejects_non_list_response():
    with pytest.raises(OutlineGenerationError, match="no sections"):
        generate_outline(_FakeClient({"oops": "not a list"}), _items())


def test_generate_outline_rejects_section_missing_title():
    client = _FakeClient([{"description": "D"}])
    with pytest.raises(OutlineGenerationError, match="invalid title"):
        generate_outline(client, _items())


def test_generate_outline_rejects_blank_description():
    client = _FakeClient([{"title": "T", "description": "   "}])
    with pytest.raises(OutlineGenerationError, match="invalid description"):
        generate_outline(client, _items())


def test_generate_outline_rejects_non_object_section():
    client = _FakeClient(["just a string"])
    with pytest.raises(OutlineGenerationError, match="not an object"):
        generate_outline(client, _items())


def test_persist_outline_links_to_requirement_and_round_trips(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'o.db'}")
    session_factory = make_session_factory(engine)

    client = _FakeClient([{"title": "Security", "description": "Describe SSO support."}])
    with session_factory() as session:
        requirement = Requirement(submitted_at=now_utc(), status="parsed", content="text")
        session.add(requirement)
        session.commit()
        session.refresh(requirement)

        draft = generate_outline(client, _items())
        outline = persist_outline(session, requirement, draft)
        session.commit()
        session.refresh(outline)

        assert outline.requirement_id == requirement.id
        assert outline.model == "fake-outline-model"
        assert len(outline.sections) == 1
        assert outline.sections[0].title == "Security"

    with session_factory() as session:
        reloaded = session.get(Requirement, requirement.id)
        assert len(reloaded.outlines) == 1
        assert reloaded.outlines[0].sections[0].description == "Describe SSO support."
