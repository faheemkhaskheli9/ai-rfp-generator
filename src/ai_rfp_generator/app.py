"""FastAPI app exposing the requirement-intake endpoint.

    uvicorn ai_rfp_generator.app:app --reload

``POST /requirements`` accepts either pasted text (multipart form field
``text``) or an uploaded file (multipart form field ``file``, one of
.txt/.docx/.pdf). Both are plain ``multipart/form-data`` fields — mixing a
JSON body with a file upload in one FastAPI endpoint isn't supported, since a
File/Form param forces the whole request to be interpreted as form data.
"""

from __future__ import annotations

from fastapi import FastAPI, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ai_rfp_generator.db import Requirement, make_engine, make_session_factory, now_utc
from ai_rfp_generator.parsing import UnsupportedFileTypeError, extract_text

app = FastAPI(title="RFP Generator — Requirement Intake")

_engine = make_engine()
_SessionFactory = make_session_factory(_engine)


class RequirementCreated(BaseModel):
    id: int
    status: str


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
        return RequirementCreated(id=requirement.id, status=requirement.status)
