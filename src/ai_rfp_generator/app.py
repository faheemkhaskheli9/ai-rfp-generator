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
    EvaluationScore,
    Fact,
    Outline,
    OutlineSection,
    Requirement,
    RequirementItem,
    SourceMaterial,
    ValidationFinding,
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
from ai_rfp_generator.evaluation import replace_evaluation_scores
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
from ai_rfp_generator.review import approve_draft, reject_draft
from ai_rfp_generator.validation import replace_validation_findings
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
    status: str
    reviewed_at: str | None


class DraftSectionsResponse(BaseModel):
    drafts: list[DraftSectionResponse]


class ValidationFindingResponse(BaseModel):
    id: int
    draft_section_id: int
    finding_type: str
    offending_text: str
    detail: str
    created_at: str


class ValidationResponse(BaseModel):
    draft_section_id: int
    valid: bool
    findings: list[ValidationFindingResponse]


class EvaluationScoreResponse(BaseModel):
    criterion: str
    score: int
    detail: str
    created_at: str


class EvaluationResponse(BaseModel):
    draft_section_id: int
    scores: list[EvaluationScoreResponse]


def _evaluation_score_response(score: EvaluationScore) -> EvaluationScoreResponse:
    return EvaluationScoreResponse(
        criterion=score.criterion,
        score=score.score,
        detail=score.detail,
        created_at=score.created_at.isoformat(),
    )


def _validation_finding_response(finding: ValidationFinding) -> ValidationFindingResponse:
    return ValidationFindingResponse(
        id=finding.id,
        draft_section_id=finding.draft_section_id,
        finding_type=finding.finding_type,
        offending_text=finding.offending_text,
        detail=finding.detail,
        created_at=finding.created_at.isoformat(),
    )


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
        status=draft.status,
        reviewed_at=draft.reviewed_at.isoformat() if draft.reviewed_at else None,
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


@app.post("/draft-sections/{draft_id}/approve", response_model=DraftSectionResponse)
async def approve_draft_section(draft_id: int) -> DraftSectionResponse:
    """Approve this draft as the single submission candidate for its section."""
    with _SessionFactory() as session:
        draft = session.get(DraftSection, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft section not found")
        approve_draft(session, draft)
        session.commit()
        session.refresh(draft)
        return _draft_section_response(draft)


@app.post("/draft-sections/{draft_id}/reject", response_model=DraftSectionResponse)
async def reject_draft_section(draft_id: int) -> DraftSectionResponse:
    """Reject one generated draft version."""
    with _SessionFactory() as session:
        draft = session.get(DraftSection, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft section not found")
        reject_draft(draft)
        session.commit()
        session.refresh(draft)
        return _draft_section_response(draft)



@app.post("/draft-sections/{draft_id}/validate", response_model=ValidationResponse)
async def validate_draft_section(draft_id: int) -> ValidationResponse:
    """Run deterministic citation validation and persist the latest findings."""
    with _SessionFactory() as session:
        draft = session.get(DraftSection, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft section not found")
        facts = (
            session.query(Fact)
            .filter(Fact.requirement_id == draft.requirement_id)
            .order_by(Fact.id)
            .all()
        )
        findings = replace_validation_findings(session, draft, facts)
        session.commit()
        for finding in findings:
            session.refresh(finding)
        return ValidationResponse(
            draft_section_id=draft.id,
            valid=not findings,
            findings=[_validation_finding_response(f) for f in findings],
        )


@app.get("/draft-sections/{draft_id}/validation", response_model=ValidationResponse)
async def get_draft_section_validation(draft_id: int) -> ValidationResponse:
    """Return persisted validation findings for a generated draft."""
    with _SessionFactory() as session:
        draft = session.get(DraftSection, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft section not found")
        findings = (
            session.query(ValidationFinding)
            .filter(ValidationFinding.draft_section_id == draft_id)
            .order_by(ValidationFinding.id)
            .all()
        )
        return ValidationResponse(
            draft_section_id=draft.id,
            valid=not findings,
            findings=[_validation_finding_response(f) for f in findings],
        )


@app.post("/draft-sections/{draft_id}/evaluate", response_model=EvaluationResponse)
async def evaluate_draft_section(draft_id: int) -> EvaluationResponse:
    """Evaluate a generated draft against the deterministic rubric."""
    with _SessionFactory() as session:
        draft = session.get(DraftSection, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft section not found")
        facts = (
            session.query(Fact)
            .filter(Fact.requirement_id == draft.requirement_id)
            .order_by(Fact.id)
            .all()
        )
        scores = replace_evaluation_scores(session, draft, facts)
        session.commit()
        for score in scores:
            session.refresh(score)
        return EvaluationResponse(
            draft_section_id=draft.id,
            scores=[_evaluation_score_response(s) for s in scores],
        )


@app.get("/draft-sections/{draft_id}/evaluation", response_model=EvaluationResponse)
async def get_draft_section_evaluation(draft_id: int) -> EvaluationResponse:
    """Return persisted evaluation scores for a generated draft."""
    with _SessionFactory() as session:
        draft = session.get(DraftSection, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft section not found")
        scores = (
            session.query(EvaluationScore)
            .filter(EvaluationScore.draft_section_id == draft_id)
            .order_by(EvaluationScore.criterion)
            .all()
        )
        return EvaluationResponse(
            draft_section_id=draft.id,
            scores=[_evaluation_score_response(s) for s in scores],
        )


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
