"""DOCX/PDF export for finalized RFP responses."""

from __future__ import annotations

import json
import os
from datetime import date
from io import BytesIO
from pathlib import Path

from docx import Document
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from ai_rfp_generator.db import DraftSection, Outline

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_CONTENT_TYPE = "application/pdf"
DEFAULT_EXPORT_CONFIG = Path("configs/export.json")


class ExportError(RuntimeError):
    """A proposal cannot be exported in its current state."""


def load_export_config() -> dict[str, str]:
    path = Path(os.environ.get("EXPORT_CONFIG_PATH", str(DEFAULT_EXPORT_CONFIG)))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except json.JSONDecodeError as exc:
        raise ExportError(f"invalid export configuration: {path}") from exc

    if not isinstance(data, dict):
        raise ExportError("export configuration must be a JSON object")

    return {
        "company_name": str(data.get("company_name") or ""),
        "submitter_name": str(data.get("submitter_name") or ""),
        "default_title": str(data.get("default_title") or "RFP Response"),
    }


def approved_draft_for_section(section) -> DraftSection | None:
    approved = [draft for draft in section.drafts if draft.status == "approved"]
    if len(approved) > 1:
        raise ExportError(
            f"outline section {section.id} has multiple approved draft versions"
        )
    return approved[0] if approved else None


def _exportable_sections(outline: Outline) -> list[tuple[object, DraftSection]]:
    if outline.status != "approved":
        raise ExportError("outline must be approved before export")

    result: list[tuple[object, DraftSection]] = []
    for section in sorted(outline.sections, key=lambda item: item.position):
        draft = approved_draft_for_section(section)
        if draft is not None:
            result.append((section, draft))

    if not result:
        raise ExportError("no approved section drafts are available for export")
    return result


def _proposal_title(outline: Outline, config: dict[str, str]) -> str:
    filename = outline.requirement.source_filename
    if filename:
        stem = Path(filename).stem.strip()
        if stem:
            return stem
    return config["default_title"]


def build_docx(outline: Outline) -> bytes:
    """Build a Word document with cover page, TOC and approved sections."""
    sections = _exportable_sections(outline)
    config = load_export_config()
    title = _proposal_title(outline, config)

    document = Document()

    document.add_heading(title, level=0)
    document.add_paragraph("RFP Response")
    if config["company_name"]:
        document.add_paragraph(config["company_name"])
    if config["submitter_name"]:
        document.add_paragraph(f"Submitted by: {config['submitter_name']}")
    document.add_paragraph(f"Submission date: {date.today().isoformat()}")
    document.add_page_break()

    document.add_heading("Table of Contents", level=1)
    for index, (section, _) in enumerate(sections, start=1):
        document.add_paragraph(f"{index}. {section.title}")
    document.add_page_break()

    for section, draft in sections:
        document.add_heading(section.title, level=1)

        if draft.validation_findings:
            warning = document.add_paragraph()
            warning.add_run("VALIDATION WARNING: ").bold = True
            warning.add_run(
                f"{len(draft.validation_findings)} unresolved validation finding(s)."
            )

        for paragraph_text in [
            part.strip() for part in draft.content.split("\n") if part.strip()
        ]:
            document.add_paragraph(paragraph_text)

    output = BytesIO()
    document.save(output)
    return output.getvalue()


def build_pdf(outline: Outline) -> bytes:
    """Build a PDF with cover page, TOC and approved sections."""
    sections = _exportable_sections(outline)
    config = load_export_config()
    title = _proposal_title(outline, config)

    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=LETTER)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("RFP Response", styles["Heading2"]))
    if config["company_name"]:
        story.append(Paragraph(config["company_name"], styles["BodyText"]))
    if config["submitter_name"]:
        story.append(
            Paragraph(f"Submitted by: {config['submitter_name']}", styles["BodyText"])
        )
    story.append(
        Paragraph(f"Submission date: {date.today().isoformat()}", styles["BodyText"])
    )
    story.append(PageBreak())

    story.append(Paragraph("Table of Contents", styles["Heading1"]))
    for index, (section, _) in enumerate(sections, start=1):
        story.append(Paragraph(f"{index}. {section.title}", styles["BodyText"]))
    story.append(PageBreak())

    for section, draft in sections:
        story.append(Paragraph(section.title, styles["Heading1"]))
        story.append(Spacer(1, 8))

        if draft.validation_findings:
            story.append(
                Paragraph(
                    f"<b>VALIDATION WARNING:</b> "
                    f"{len(draft.validation_findings)} unresolved validation finding(s).",
                    styles["BodyText"],
                )
            )
            story.append(Spacer(1, 6))

        for paragraph_text in [
            part.strip() for part in draft.content.split("\n") if part.strip()
        ]:
            story.append(Paragraph(paragraph_text, styles["BodyText"]))
            story.append(Spacer(1, 6))

    document.build(story)
    return output.getvalue()
