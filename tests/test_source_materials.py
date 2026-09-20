from pathlib import Path

import pytest

from ai_rfp_generator.db import Requirement, make_engine, make_session_factory, now_utc
from ai_rfp_generator.source_materials import (
    SourceMaterialUploadError,
    UploadedFile,
    store_source_materials,
)


@pytest.fixture
def session_and_requirement(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_MATERIALS_DIR", str(tmp_path / "materials"))
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


def test_store_writes_file_atomically_and_persists_row(session_and_requirement):
    session, requirement = session_and_requirement

    results = store_source_materials(
        session, requirement, [UploadedFile(filename="past_proposal.txt", raw=b"We delivered X for client Y.")]
    )
    session.commit()

    assert len(results) == 1
    stored = results[0]
    assert stored.original_filename == "past_proposal.txt"
    assert stored.extension == ".txt"
    assert stored.size_bytes == len(b"We delivered X for client Y.")
    assert stored.extracted_text == "We delivered X for client Y."

    stored_path = Path(stored.stored_path)
    assert stored_path.is_file()
    assert stored_path.read_bytes() == b"We delivered X for client Y."
    # No leftover temp file from the atomic write.
    leftovers = [p for p in stored_path.parent.iterdir() if p.name.startswith(".tmp-upload-")]
    assert leftovers == []


def test_multiple_files_in_one_batch_are_all_stored(session_and_requirement):
    session, requirement = session_and_requirement

    results = store_source_materials(
        session,
        requirement,
        [
            UploadedFile(filename="case_study_a.txt", raw=b"Case study A content"),
            UploadedFile(filename="case_study_b.txt", raw=b"Case study B content"),
        ],
    )
    session.commit()

    assert len(results) == 2
    assert {r.original_filename for r in results} == {"case_study_a.txt", "case_study_b.txt"}
    assert len({r.content_hash for r in results}) == 2


def test_reuploading_identical_content_is_idempotent(session_and_requirement):
    session, requirement = session_and_requirement
    raw = b"Identical capability statement content."

    first = store_source_materials(session, requirement, [UploadedFile(filename="cap.txt", raw=raw)])
    session.commit()
    second = store_source_materials(
        session, requirement, [UploadedFile(filename="cap_renamed.txt", raw=raw)]
    )
    session.commit()

    assert first[0].id == second[0].id
    stored_dir = Path(first[0].stored_path).parent
    stored_files = list(stored_dir.iterdir())
    assert len(stored_files) == 1


def test_unsupported_extension_raises_before_writing_anything(session_and_requirement):
    session, requirement = session_and_requirement

    with pytest.raises(SourceMaterialUploadError, match="unsupported"):
        store_source_materials(
            session,
            requirement,
            [
                UploadedFile(filename="ok.txt", raw=b"fine"),
                UploadedFile(filename="scan.png", raw=b"\x89PNG\r\n"),
            ],
        )

    from ai_rfp_generator.source_materials import source_materials_dir

    base_dir = source_materials_dir() / str(requirement.id)
    assert not base_dir.exists()


def test_empty_file_raises_before_writing_anything(session_and_requirement):
    session, requirement = session_and_requirement

    with pytest.raises(SourceMaterialUploadError, match="empty"):
        store_source_materials(session, requirement, [UploadedFile(filename="blank.txt", raw=b"")])


def test_empty_batch_raises(session_and_requirement):
    session, requirement = session_and_requirement

    with pytest.raises(SourceMaterialUploadError, match="no files"):
        store_source_materials(session, requirement, [])
