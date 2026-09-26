"""Offline end-to-end example for grounded RFP section drafting.

Run:
    PYTHONPATH=src python examples/generate_grounded_draft_example.py

No API key or network access is required. Fake LLM clients are used while the
real parsing, persistence, outline approval, fact extraction, retrieval,
citation validation, and draft persistence code paths are exercised.
"""

from __future__ import annotations

from tempfile import TemporaryDirectory

from ai_rfp_generator.db import (
    Requirement,
    RequirementItem,
    SourceMaterial,
    make_engine,
    make_session_factory,
    now_utc,
)
from ai_rfp_generator.drafting import generate_section_draft, persist_section_draft
from ai_rfp_generator.facts import extract_and_persist_facts
from ai_rfp_generator.normalize import normalize_text
from ai_rfp_generator.outline import approve_outline, generate_outline, persist_outline


class FakeOutlineClient:
    model_name = "offline-outline"

    def generate(self, requirement_text: str) -> list[dict]:
        return [
            {
                "title": "Security and Hosting",
                "description": "Explain cloud hosting, encryption, and support capabilities.",
            },
            {
                "title": "Delivery Approach",
                "description": "Explain relevant delivery experience and implementation approach.",
            },
        ]


class FakeDraftClient:
    model_name = "offline-drafter"

    def generate(self, *, section_title, section_description, facts, strategy):
        first = facts[0]
        return (
            f"{section_title}: {first.text} [F{first.id}] "
            "Additional details should be reviewed by the proposal owner."
        )


def main() -> None:
    with TemporaryDirectory() as temp_dir:
        engine = make_engine(f"sqlite:///{temp_dir}/example.db")
        factory = make_session_factory(engine)

        with factory() as session:
            requirement = Requirement(
                source_filename="customer-rfp.txt",
                submitted_at=now_utc(),
                status="received",
                content=(
                    "Describe your cloud hosting and encryption controls.\n"
                    "Explain your implementation experience."
                ),
            )
            session.add(requirement)
            session.flush()

            normalized = normalize_text(requirement.content)
            for item in normalized:
                session.add(
                    RequirementItem(
                        requirement_id=requirement.id,
                        position=item.position,
                        item_type=item.item_type,
                        content=item.content,
                    )
                )
            requirement.status = "parsed"
            session.commit()
            session.refresh(requirement)

            outline_draft = generate_outline(FakeOutlineClient(), requirement.items)
            outline = persist_outline(session, requirement, outline_draft)
            session.commit()
            session.refresh(outline)
            approve_outline(outline)
            session.commit()

            source_text = (
                "The platform can be hosted in public cloud environments with encryption at rest. "
                "Our delivery team has implemented enterprise software projects for regulated customers."
            )
            source = SourceMaterial(
                requirement_id=requirement.id,
                original_filename="capability-statement.txt",
                stored_path=f"{temp_dir}/capability-statement.txt",
                content_hash="offline-example",
                extension=".txt",
                size_bytes=len(source_text.encode()),
                extracted_text=source_text,
                uploaded_at=now_utc(),
            )
            session.add(source)
            session.commit()
            session.refresh(source)

            facts = extract_and_persist_facts(session, source)
            session.commit()

            print(f"Requirement {requirement.id}: {len(requirement.items)} parsed items")
            print(f"Outline {outline.id}: {len(outline.sections)} approved sections")
            print(f"Facts: {len(facts)} extracted\n")

            for section in outline.sections:
                result = generate_section_draft(
                    FakeDraftClient(),
                    section,
                    requirement.facts,
                    strategy="concise",
                )
                draft = persist_section_draft(
                    session,
                    section,
                    result,
                    strategy="concise",
                )
                session.commit()
                print(f"## {section.title}")
                print(draft.content)
                print()


if __name__ == "__main__":
    main()
