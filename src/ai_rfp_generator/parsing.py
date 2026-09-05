"""Extract plain text from an uploaded requirements document."""

from __future__ import annotations

from pathlib import Path


class UnsupportedFileTypeError(ValueError):
    def __init__(self, extension: str) -> None:
        super().__init__(f"unsupported file type {extension!r} (expected .txt, .docx, or .pdf)")
        self.extension = extension


def extract_text(filename: str, raw: bytes) -> str:
    """Return the plain-text content of ``raw`` bytes named ``filename``."""
    extension = Path(filename).suffix.lower()

    if extension == ".txt":
        return raw.decode("utf-8", errors="replace")

    if extension == ".docx":
        import io

        from docx import Document

        document = Document(io.BytesIO(raw))
        return "\n".join(p.text for p in document.paragraphs)

    if extension == ".pdf":
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    raise UnsupportedFileTypeError(extension)
