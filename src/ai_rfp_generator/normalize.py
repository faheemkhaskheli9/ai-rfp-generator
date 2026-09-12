"""Normalize raw requirement text into discrete, typed items.

Downstream pipeline stages (outline generation, fact extraction, section
drafting) need structured requirement data — sections, questions, and
deadlines — rather than re-parsing free text on every read. This module is
the one place that turns a :class:`~ai_rfp_generator.db.Requirement`'s raw
``content`` into a flat, ordered list of :class:`ParsedItem` records.

Classification is deterministic (regex/heuristics), not LLM-based: the intake
text at this stage is short, unstructured plain text extracted from
.txt/.docx/.pdf uploads, and a cheap rule-based pass is enough to bucket each
line. LLM-generated structure (the document *outline*) is a separate,
downstream concern (see the outline-generation issue).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SECTION = "section"
QUESTION = "question"
DEADLINE = "deadline"
REQUIREMENT = "requirement"

# Markdown-style heading ("# Foo", "## Foo"), numbered heading ("1. Foo",
# "1) Foo"), or a short ALL-CAPS/Title-style line ending in ':' — the common
# ways a plain-text/extracted-docx requirements doc marks a section header.
_SECTION_RE = re.compile(r"^(#{1,6}\s+\S|(\d+[.)]\s+\S)|([A-Z][A-Za-z0-9 /&-]{2,60}:)\s*$)")

# A date-shaped token (2026-09-30, 09/30/2026, "September 30, 2026") or an
# explicit deadline/due keyword.
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
_DEADLINE_KEYWORD_RE = re.compile(r"\b(deadline|due date|due by|due on|submit(ted)? by|no later than)\b", re.IGNORECASE)

# Control characters other than the whitespace ones (\t \n \r) — a sign the
# "text" is actually undecoded binary that slipped past extraction.
_BINARY_GARBAGE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class NormalizationError(ValueError):
    """Raised when ``content`` cannot be split into any normalized items."""


@dataclass(frozen=True)
class ParsedItem:
    position: int
    item_type: str
    content: str


def classify_line(line: str) -> str:
    """Return the item type (`section`/`question`/`deadline`/`requirement`) for one line."""
    stripped = line.strip()

    if stripped.endswith("?"):
        return QUESTION
    if _DEADLINE_KEYWORD_RE.search(stripped) or _DATE_RE.search(stripped):
        return DEADLINE
    if _SECTION_RE.match(stripped):
        return SECTION
    return REQUIREMENT


def normalize_text(content: str) -> list[ParsedItem]:
    """Split ``content`` into an ordered list of classified :class:`ParsedItem`.

    Raises :class:`NormalizationError` if ``content`` has no extractable lines
    (only whitespace, or binary garbage that slipped through text extraction)
    — callers should catch this, log it, and mark the source record with a
    retryable failure status rather than silently dropping the submission.
    """
    if _BINARY_GARBAGE_RE.search(content):
        raise NormalizationError("content contains non-text binary data")

    lines = [line.strip() for line in content.splitlines()]
    lines = [line for line in lines if line]

    if not lines:
        raise NormalizationError("content has no extractable lines")

    return [ParsedItem(position=i, item_type=classify_line(line), content=line) for i, line in enumerate(lines)]
