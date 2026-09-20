"""Store uploaded source materials (past proposals, case studies, capability
statements) linked to a :class:`~ai_rfp_generator.db.Requirement`, for the
Phase 2 fact-extraction step to draw cited claims from.

Storage is a plain directory tree (``SOURCE_MATERIALS_DIR``, defaulting to a
local ``./source_materials`` folder so tests and local runs need no external
blob service — the same "env-var-with-local-default" convention ``db.py``
uses for ``DATABASE_URL``). Each file is content-addressed: named after the
sha256 hash of its raw bytes rather than its original filename, so:

* re-uploading byte-identical content for the same requirement is idempotent
  (same hash -> same path -> no duplicate write, no duplicate row), matching
  the "deterministic identity from content" convention this repo's ingestion
  patterns follow elsewhere;
* the on-disk filename can never collide across unrelated uploads.

Every write goes through :func:`_write_atomically`: content is written to a
temp file in the same directory and moved into place with ``os.replace``, so
a failure mid-write (disk full, killed process) never leaves a truncated file
at the final, hash-named path — that in turn makes the "does this hash's file
already exist" dedup check safe to rely on as a completion marker, since a
partially-written file can never reach that path.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ai_rfp_generator.db import Requirement, SourceMaterial, now_utc
from ai_rfp_generator.parsing import UnsupportedFileTypeError, extract_text


class SourceMaterialUploadError(ValueError):
    """One or more uploaded files could not be validated/stored.

    Raised before any file in the batch is written or persisted, so a request
    with one bad file among several never leaves a partial set of stored
    materials behind.
    """


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    raw: bytes


def source_materials_dir() -> Path:
    return Path(os.environ.get("SOURCE_MATERIALS_DIR", "./source_materials"))


def _content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_atomically(target_path: Path, raw: bytes) -> None:
    """Write ``raw`` to ``target_path``, atomically.

    A no-op if ``target_path`` already exists: because every write to this
    path goes through this same atomic temp-file-then-replace sequence, and
    the path is named after the content's own hash, an existing file at this
    exact path is guaranteed to already hold this exact content — there is no
    partially-written state that could hide behind it.
    """
    if target_path.exists():
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=target_path.parent, prefix=".tmp-upload-", suffix=target_path.suffix
    )
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(raw)
        os.replace(tmp_name, target_path)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def store_source_materials(
    session, requirement: Requirement, files: list[UploadedFile]
) -> list[SourceMaterial]:
    """Validate, store, and persist ``files`` as source materials for ``requirement``.

    Raises :class:`SourceMaterialUploadError` if the batch is empty, any file
    is empty, or any file's extension isn't one :func:`~ai_rfp_generator.parsing.extract_text`
    supports (.txt/.docx/.pdf) — validation runs over the whole batch before
    any file is written or added to the session, so a rejected request has no
    side effects.

    Returns the list of :class:`SourceMaterial` rows, in upload order. A file
    whose content hash already matches an existing source material on this
    requirement is *not* re-stored or duplicated — the existing row is
    returned in its place (idempotent re-upload).
    """
    if not files:
        raise SourceMaterialUploadError("no files were submitted")

    validated: list[tuple[UploadedFile, str, str, str]] = []
    for uploaded in files:
        if not uploaded.raw:
            raise SourceMaterialUploadError(f"{uploaded.filename!r}: file is empty")
        try:
            extracted_text = extract_text(uploaded.filename, uploaded.raw)
        except UnsupportedFileTypeError as exc:
            raise SourceMaterialUploadError(f"{uploaded.filename!r}: {exc}") from exc
        extension = Path(uploaded.filename).suffix.lower()
        validated.append((uploaded, extension, _content_hash(uploaded.raw), extracted_text))

    base_dir = source_materials_dir() / str(requirement.id)
    existing_by_hash = {sm.content_hash: sm for sm in requirement.source_materials}

    results: list[SourceMaterial] = []
    for uploaded, extension, content_hash, extracted_text in validated:
        existing = existing_by_hash.get(content_hash)
        if existing is not None:
            results.append(existing)
            continue

        stored_path = base_dir / f"{content_hash}{extension}"
        _write_atomically(stored_path, uploaded.raw)

        source_material = SourceMaterial(
            original_filename=uploaded.filename,
            stored_path=str(stored_path),
            content_hash=content_hash,
            extension=extension,
            size_bytes=len(uploaded.raw),
            extracted_text=extracted_text,
            uploaded_at=now_utc(),
        )
        # Go through the relationship (not just setting requirement_id
        # directly) so requirement.source_materials stays in sync in memory
        # too -- otherwise a second store_source_materials() call in the same
        # session sees a stale, pre-insert copy of this collection and the
        # dedup check above misses a row it just added.
        requirement.source_materials.append(source_material)
        session.add(source_material)
        results.append(source_material)
        existing_by_hash[content_hash] = source_material

    return results
