"""FastAPI app exposing the requirement-intake endpoint.

    uvicorn ai_rfp_generator.app:app --reload

``POST /requirements`` accepts either pasted text (multipart form field
``text``) or an uploaded file (multipart form field ``file``, one of
.txt/.docx/.pdf). Both are plain ``multipart/form-data`` fields — mixing a
JSON body with a file upload in one FastAPI endpoint isn't supported, since a
File/Form param forces the whole request to be interpreted as form data.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ai_rfp_generator.db import (
    DraftSection,
    Fact,
    Outline,
    OutlineSection,
    Requirement,
    RequirementItem,
    SourceMaterial,
    make_engine,
    make_session_factory,
    now_utc,
)
from ai_rfp_generator.drafting import (
    OpenAISectionDraftingClient,
    SectionDraftingError,
    generate_section_draft,
    persist_section_draft,
)
from ai_rfp_generator.facts import extract_and_persist_facts
from ai_rfp_generator.normalize import NormalizationError, normalize_text
from ai_rfp_generator.outline import (
    OpenAIOutlineClient,
    OutlineEditError,
    OutlineGenerationError,
    apply_outline_edit,
    approve_outline,
    generate_outline,
    persist_outline,
    reject_outline,
)
from ai_rfp_generator.parsing import UnsupportedFileTypeError, extract_text
from ai_rfp_generator.source_materials import (
    SourceMaterialUploadError,
    UploadedFile,
    store_source_materials,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="RFP Generator — Requirement Intake")

_engine = make_engine()
_SessionFactory = make_session_factory(_engine)


class RequirementCreated(BaseModel):
    id: int
    status: str


class OutlineSectionResponse(BaseModel):
    position: int
    title: str
    description: str


class OutlineResponse(BaseModel):
    id: int
    requirement_id: int
    model: str
    status: str
    generated_at: str
    updated_at: str | None
    sections: list[OutlineSectionResponse]


class SourceMaterialResponse(BaseModel):
    id: int
    requirement_id: int
    original_filename: str
    extension: str
    size_bytes: int
    content_hash: str
    uploaded_at: str


class SourceMaterialsUploaded(BaseModel):
    source_materials: list[SourceMaterialResponse]


class FactResponse(BaseModel):
    id: int
    requirement_id: int
    source_material_id: int
    text: str
    start_offset: int
    end_offset: int
    is_duplicate: bool
    duplicate_of_id: int | None
    extracted_at: str


class FactsExtracted(BaseModel):
    facts: list[FactResponse]


class OutlineSectionEdit(BaseModel):
    title: str
    description: str


class OutlineEditRequest(BaseModel):
    sections: list[OutlineSectionEdit]


class DraftSectionResponse(BaseModel):
    id: int
    requirement_id: int
    outline_section_id: int
    strategy: str
    model: str
    content: str
    fact_ids: list[int]
    generated_at: str


class DraftSectionsResponse(BaseModel):
    drafts: list[DraftSectionResponse]


def _draft_section_response(draft: DraftSection) -> DraftSectionResponse:
    import json

    return DraftSectionResponse(
        id=draft.id,
        requirement_id=draft.requirement_id,
        outline_section_id=draft.outline_section_id,
        strategy=draft.strategy,
        model=draft.model,
        content=draft.content,
        fact_ids=json.loads(draft.fact_ids_json),
        generated_at=draft.generated_at.isoformat(),
    )


def _outline_response(outline: Outline) -> "OutlineResponse":
    return OutlineResponse(
        id=outline.id,
        requirement_id=outline.requirement_id,
        model=outline.model,
        status=outline.status,
        generated_at=outline.generated_at.isoformat(),
        updated_at=outline.updated_at.isoformat() if outline.updated_at else None,
        sections=[
            OutlineSectionResponse(position=s.position, title=s.title, description=s.description)
            for s in outline.sections
        ],
    )


@app.post("/requirements", response_model=RequirementCreated, status_code=201)
async def submit_requirement(
    text: str | None = Form(default=None), file: UploadFile | None = None
) -> RequirementCreated:
    if file is not None:
        raw = await file.read()
        try:
            content = extract_text(file.filename or "", raw)
        except UnsupportedFileTypeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        source_filename = file.filename
    elif text is not None:
        content = text
        source_filename = None
    else:
        raise HTTPException(status_code=400, detail="submit either 'text' or a 'file' upload")

    if not content.strip():
        raise HTTPException(status_code=400, detail="submission is empty")

    with _SessionFactory() as session:
        requirement = Requirement(
            source_filename=source_filename,
            submitted_at=now_utc(),
            status="received",
            content=content,
        )
        session.add(requirement)
        session.commit()
        session.refresh(requirement)

        _normalize_and_store(session, requirement)
        session.commit()
        session.refresh(requirement)

        return RequirementCreated(id=requirement.id, status=requirement.status)


def _source_material_response(source_material: SourceMaterial) -> SourceMaterialResponse:
    return SourceMaterialResponse(
        id=source_material.id,
        requirement_id=source_material.requirement_id,
        original_filename=source_material.original_filename,
        extension=source_material.extension,
        size_bytes=source_material.size_bytes,
        content_hash=source_material.content_hash,
        uploaded_at=source_material.uploaded_at.isoformat(),
    )


@app.post(
    "/requirements/{requirement_id}/source-materials",
    response_model=SourceMaterialsUploaded,
    status_code=201,
)
async def upload_source_materials(
    requirement_id: int, files: list[UploadFile] = File(...)
) -> SourceMaterialsUploaded:
    """Upload one or more source documents (past proposals, case studies,
    capability statements) linked to ``requirement_id`` for later fact
    extraction (Phase 2). Rejects the whole batch with 400 if any file is
    empty or an unsupported type (.txt/.docx/.pdf only) — nothing is stored
    unless every file in the request is valid.
    """
    with _SessionFactory() as session:
        requirement = session.get(Requirement, requirement_id)
        if requirement is None:
            raise HTTPException(status_code=404, detail="requirement not found")

        uploaded = [UploadedFile(filename=f.filename or "", raw=await f.read()) for f in files]
        try:
            stored = store_source_materials(session, requirement, uploaded)
        except SourceMaterialUploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session.commit()
        for source_material in stored:
            session.refresh(source_material)

        return SourceMaterialsUploaded(
            source_materials=[_source_material_response(sm) for sm in stored]
        )


def _fact_response(fact: Fact) -> FactResponse:
    return FactResponse(
        id=fact.id,
        requirement_id=fact.requirement_id,
        source_material_id=fact.source_material_id,
        text=fact.text,
        start_offset=fact.start_offset,
        end_offset=fact.end_offset,
        is_duplicate=fact.is_duplicate,
        duplicate_of_id=fact.duplicate_of_id,
        extracted_at=fact.extracted_at.isoformat(),
    )


@app.post("/requirements/{requirement_id}/facts", response_model=FactsExtracted, status_code=201)
async def extract_requirement_facts(requirement_id: int) -> FactsExtracted:
    """Extract cited facts from every source material uploaded for
    ``requirement_id`` that hasn't been processed yet (a source material with
    at least one existing ``Fact`` row is treated as already extracted, so
    re-calling this is a no-op for materials already covered rather than
    creating a fresh, redundant set of duplicate-flagged facts each time).
    """
    with _SessionFactory() as session:
        requirement = session.get(Requirement, requirement_id)
        if requirement is None:
            raise HTTPException(status_code=404, detail="requirement not found")
        if not requirement.source_materials:
            raise HTTPException(
                status_code=422, detail="requirement has no source materials to extract facts from"
            )

        newly_extracted: list[Fact] = []
        for source_material in requirement.source_materials:
            already_processed = (
                session.query(Fact.id)
                .filter(Fact.source_material_id == source_material.id)
                .first()
                is not None
            )
            if already_processed:
                continue
            newly_extracted.extend(extract_and_persist_facts(session, source_material))

        session.commit()
        for fact in newly_extracted:
            session.refresh(fact)

        return FactsExtracted(facts=[_fact_response(f) for f in newly_extracted])


@app.get("/requirements/{requirement_id}/facts", response_model=FactsExtracted)
async def list_requirement_facts(requirement_id: int) -> FactsExtracted:
    """List every fact extracted so far for ``requirement_id``, each with its
    citation reference, for the later validation pass to consume.
    """
    with _SessionFactory() as session:
        requirement = session.get(Requirement, requirement_id)
        if requirement is None:
            raise HTTPException(status_code=404, detail="requirement not found")
        return FactsExtracted(facts=[_fact_response(f) for f in requirement.facts])


@app.post(
    "/requirements/{requirement_id}/outline", response_model=OutlineResponse, status_code=201
)
async def generate_requirement_outline(requirement_id: int) -> OutlineResponse:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503, detail="outline generation unavailable: OPENAI_API_KEY not set"
        )

    with _SessionFactory() as session:
        requirement = session.get(Requirement, requirement_id)
        if requirement is None:
            raise HTTPException(status_code=404, detail="requirement not found")
        if not requirement.items:
            raise HTTPException(
                status_code=422, detail="requirement has no parsed items to outline"
            )

        client = OpenAIOutlineClient(api_key)
        try:
            draft = generate_outline(client, requirement.items)
        except OutlineGenerationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        outline = persist_outline(session, requirement, draft)
        session.commit()
        session.refresh(outline)

        return _outline_response(outline)


def _get_outline_or_404(session, outline_id: int) -> Outline:
    outline = session.get(Outline, outline_id)
    if outline is None:
        raise HTTPException(status_code=404, detail="outline not found")
    return outline


@app.get("/outlines/{outline_id}", response_model=OutlineResponse)
async def get_outline(outline_id: int) -> OutlineResponse:
    """Fetch an outline for human review, with its current approval status."""
    with _SessionFactory() as session:
        outline = _get_outline_or_404(session, outline_id)
        return _outline_response(outline)


@app.patch("/outlines/{outline_id}", response_model=OutlineResponse)
async def edit_outline(outline_id: int, edit: OutlineEditRequest) -> OutlineResponse:
    """Apply a reviewer's edit (reorder/rename/add/remove sections).

    ``sections`` is the full desired ordered list — list order becomes the
    new section positions. The prior sections are snapshotted into a revision
    before being replaced, and if the outline had already been approved or
    rejected, editing it resets its status back to ``draft`` so the edited
    content requires a fresh approval.
    """
    with _SessionFactory() as session:
        outline = _get_outline_or_404(session, outline_id)
        try:
            apply_outline_edit(session, outline, [s.model_dump() for s in edit.sections])
        except OutlineEditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
        session.refresh(outline)
        return _outline_response(outline)


@app.post("/outlines/{outline_id}/approve", response_model=OutlineResponse)
async def approve_outline_endpoint(outline_id: int) -> OutlineResponse:
    """Explicitly approve an outline, gating Phase 2 drafting open for it."""
    with _SessionFactory() as session:
        outline = _get_outline_or_404(session, outline_id)
        approve_outline(outline)
        session.commit()
        session.refresh(outline)
        return _outline_response(outline)


@app.post("/outlines/{outline_id}/reject", response_model=OutlineResponse)
async def reject_outline_endpoint(outline_id: int) -> OutlineResponse:
    """Explicitly reject an outline (reviewer wants further edits)."""
    with _SessionFactory() as session:
        outline = _get_outline_or_404(session, outline_id)
        reject_outline(outline)
        session.commit()
        session.refresh(outline)
        return _outline_response(outline)


@app.post(
    "/outline-sections/{section_id}/drafts",
    response_model=DraftSectionResponse,
    status_code=201,
)
async def generate_outline_section_draft(
    section_id: int,
    strategy: str = "detailed",
) -> DraftSectionResponse:
    """Generate a new grounded draft version for one approved outline section."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="section drafting unavailable: OPENAI_API_KEY not set",
        )

    with _SessionFactory() as session:
        section = session.get(OutlineSection, section_id)
        if section is None:
            raise HTTPException(status_code=404, detail="outline section not found")

        facts = (
            session.query(Fact)
            .filter(Fact.requirement_id == section.outline.requirement_id)
            .order_by(Fact.id)
            .all()
        )
        client = OpenAISectionDraftingClient(api_key)
        try:
            result = generate_section_draft(client, section, facts, strategy=strategy)
        except SectionDraftingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        draft = persist_section_draft(session, section, result, strategy=strategy)
        session.commit()
        session.refresh(draft)
        return _draft_section_response(draft)


@app.get(
    "/outline-sections/{section_id}/drafts",
    response_model=DraftSectionsResponse,
)
async def list_outline_section_drafts(section_id: int) -> DraftSectionsResponse:
    """Return all immutable draft versions for an outline section."""
    with _SessionFactory() as session:
        section = session.get(OutlineSection, section_id)
        if section is None:
            raise HTTPException(status_code=404, detail="outline section not found")
        drafts = (
            session.query(DraftSection)
            .filter(DraftSection.outline_section_id == section_id)
            .order_by(DraftSection.generated_at, DraftSection.id)
            .all()
        )
        return DraftSectionsResponse(drafts=[_draft_section_response(d) for d in drafts])


def _normalize_and_store(session, requirement: Requirement) -> None:
    """Parse ``requirement.content`` into normalized items and attach them.

    On a malformed submission this logs the failure and marks the requirement
    with a retryable ``parse_failed`` status instead of dropping it silently
    or failing the whole intake request — the raw content is already
    persisted, so a later reparse pass (or resubmission) can recover it.
    """
    try:
        parsed_items = normalize_text(requirement.content)
    except NormalizationError:
        logger.exception("failed to normalize requirement id=%s into items", requirement.id)
        requirement.status = "parse_failed"
        return

    for item in parsed_items:
        session.add(
            RequirementItem(
                requirement_id=requirement.id,
                position=item.position,
                item_type=item.item_type,
                content=item.content,
            )
        )
    requirement.status = "parsed"
