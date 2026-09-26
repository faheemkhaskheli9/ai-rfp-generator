"""Generate sample DOCX and PDF exports without network/API access.

Run:
    PYTHONPATH=src python examples/generate_export_example.py
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from ai_rfp_generator.db import (
    DraftSection,
    Outline,
    OutlineSection,
    Requirement,
    make_engine,
    make_session_factory,
    now_utc,
)
from ai_rfp_generator.export import build_docx, build_pdf


def main() -> None:
    with TemporaryDirectory() as temp_dir:
        engine = make_engine(f"sqlite:///{temp_dir}/export-example.db")
        factory = make_session_factory(engine)

        with factory() as session:
            requirement = Requirement(
                source_filename="sample-customer-rfp.txt",
                submitted_at=now_utc(),
                status="parsed",
                content="Describe the proposed solution and implementation approach.",
            )
            session.add(requirement)
            session.flush()

            outline = Outline(
                requirement_id=requirement.id,
                model="offline-example",
                generated_at=now_utc(),
                status="approved",
                reviewed_at=now_utc(),
            )
            session.add(outline)
            session.flush()

            sections = [
                OutlineSection(
                    outline_id=outline.id,
                    position=0,
                    title="Proposed Solution",
                    description="Describe the solution.",
                ),
                OutlineSection(
                    outline_id=outline.id,
                    position=1,
                    title="Implementation Approach",
                    description="Describe implementation.",
                ),
            ]
            session.add_all(sections)
            session.flush()

            session.add_all(
                [
                    DraftSection(
                        requirement_id=requirement.id,
                        outline_section_id=sections[0].id,
                        strategy="concise",
                        model="offline-example",
                        content="The proposed solution addresses the requested scope.",
                        fact_ids_json="[]",
                        generated_at=now_utc(),
                        status="approved",
                        reviewed_at=now_utc(),
                    ),
                    DraftSection(
                        requirement_id=requirement.id,
                        outline_section_id=sections[1].id,
                        strategy="detailed",
                        model="offline-example",
                        content="Implementation is organized into phased delivery and review steps.",
                        fact_ids_json="[]",
                        generated_at=now_utc(),
                        status="approved",
                        reviewed_at=now_utc(),
                    ),
                ]
            )
            session.commit()
            session.refresh(outline)

            output_dir = Path("examples/output")
            output_dir.mkdir(parents=True, exist_ok=True)
            docx_path = output_dir / "sample-rfp-response.docx"
            pdf_path = output_dir / "sample-rfp-response.pdf"
            docx_path.write_bytes(build_docx(outline))
            pdf_path.write_bytes(build_pdf(outline))

            print(f"Wrote {docx_path}")
            print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    main()
