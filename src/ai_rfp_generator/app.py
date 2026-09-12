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

from fastapi import FastAPI, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ai_rfp_generator.db import Requirement, RequirementItem, make_engine, make_session_factory, now_utc
from ai_rfp_generator.normalize import NormalizationError, normalize_text
from ai_rfp_generator.outline import OpenAIOutlineClient, OutlineGenerationError, generate_outline, persist_outline
from ai_rfp_generator.parsing import UnsupportedFileTypeError, extract_text

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
    sections: list[OutlineSectionResponse]


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

        return OutlineResponse(
            id=outline.id,
            requirement_id=outline.requirement_id,
            model=outline.model,
            sections=[
                OutlineSectionResponse(position=s.position, title=s.title, description=s.description)
                for s in outline.sections
            ],
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
