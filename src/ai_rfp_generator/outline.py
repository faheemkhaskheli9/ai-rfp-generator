"""LLM-generated document outline from normalized requirement items (Phase 1).

The LLM call sits behind :class:`OutlineGeneratorClient`, and the model is a
configuration value (constructor arg, else ``$OUTLINE_MODEL``, else
``DEFAULT_MODEL``) rather than hardcoded — the "provider/model choices
swappable behind an interface" convention this repo's `docs/architecture.md`
points at `multi-llm-router` for. A completed API call tells us nothing about
whether the payload is usable, so the response is validated (a non-empty,
ordered list of ``{title, description}`` sections) before being trusted or
persisted; a malformed response raises :class:`OutlineGenerationError` rather
than silently producing an empty or garbled outline.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from ai_rfp_generator.db import Outline, OutlineRevision, OutlineSection, Requirement, RequirementItem
from ai_rfp_generator.prompts import load_prompt

DEFAULT_MODEL = "gpt-4o-mini"

#: Outline.status values. "draft" gates Phase 2 drafting off until a human
#: reviewer explicitly approves; editing a reviewed outline drops it back to
#: "draft" so an approval can never silently cover changed content.
STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
VALID_STATUSES = frozenset({STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED})


class OutlineGenerationError(RuntimeError):
    """The outline-generation call failed, or its response can't be trusted."""


class OutlineEditError(RuntimeError):
    """A proposed edit to an outline's sections isn't a valid outline."""


class OutlineNotApprovedError(RuntimeError):
    """Raised by :func:`require_outline_approved` when a caller downstream of
    review (e.g. Phase 2 section drafting) tries to proceed on an outline that
    hasn't been explicitly approved by a human reviewer.
    """


@dataclass(frozen=True)
class OutlineSectionDraft:
    position: int
    title: str
    description: str


@dataclass(frozen=True)
class OutlineDraft:
    sections: tuple[OutlineSectionDraft, ...]
    model: str
    generated_at: str  # ISO-8601 UTC timestamp


class OutlineGeneratorClient(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate(self, requirement_text: str) -> list[dict]:
        """Return an ordered list of ``{"title": str, "description": str}``.

        May raise on API/network/parsing failure.
        """
        ...


def _build_requirement_text(items: list[RequirementItem]) -> str:
    ordered = sorted(items, key=lambda item: item.position)
    return "\n".join(f"[{item.item_type}] {item.content}" for item in ordered)


def _validate_sections(
    raw_sections: object, *, error: type[Exception], source: str = "outline generator"
) -> list[OutlineSectionDraft]:
    """Validate ``raw_sections`` into an ordered, non-empty list of sections.

    Shared by :func:`generate_outline` (validating an LLM response) and
    :func:`apply_outline_edit` (validating a human reviewer's edit) so both
    entry points reject a malformed/empty section list the same way instead
    of drifting apart. ``error`` is the exception type to raise and ``source``
    names the caller in the message, so each surfaces a failure that matches
    its own domain.
    """
    if not isinstance(raw_sections, list) or not raw_sections:
        raise error(f"{source} returned no sections (got {raw_sections!r})")

    sections: list[OutlineSectionDraft] = []
    for index, entry in enumerate(raw_sections):
        if not isinstance(entry, dict):
            raise error(f"section {index} is not an object: {entry!r}")
        title, description = entry.get("title"), entry.get("description")
        if not isinstance(title, str) or not title.strip():
            raise error(f"section {index} has an invalid title: {title!r}")
        if not isinstance(description, str) or not description.strip():
            raise error(f"section {index} has an invalid description: {description!r}")
        sections.append(OutlineSectionDraft(position=index, title=title.strip(), description=description.strip()))
    return sections


def generate_outline(client: OutlineGeneratorClient, items: list[RequirementItem]) -> OutlineDraft:
    """Generate and validate an outline for ``items``.

    Raises :class:`OutlineGenerationError` if there are no items to outline,
    or if the client's response isn't a non-empty ordered list of sections
    each with a non-blank title and description.
    """
    if not items:
        raise OutlineGenerationError("cannot generate an outline from zero requirement items")

    raw_sections = client.generate(_build_requirement_text(items))
    sections = _validate_sections(raw_sections, error=OutlineGenerationError)

    return OutlineDraft(
        sections=tuple(sections),
        model=client.model_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def persist_outline(session, requirement: Requirement, draft: OutlineDraft) -> Outline:
    """Store ``draft`` as a new :class:`Outline` linked to ``requirement``."""
    outline = Outline(
        requirement_id=requirement.id,
        model=draft.model,
        generated_at=datetime.fromisoformat(draft.generated_at),
        status=STATUS_DRAFT,
    )
    outline.sections = [
        OutlineSection(position=s.position, title=s.title, description=s.description)
        for s in draft.sections
    ]
    session.add(outline)
    return outline


def _snapshot_sections(outline: Outline) -> OutlineRevision:
    """Build (but don't add to the session) a revision snapshotting
    ``outline``'s current sections, so the pre-edit state — including the
    original LLM-generated outline, on the first edit — isn't lost.
    """
    payload = [
        {"position": s.position, "title": s.title, "description": s.description}
        for s in sorted(outline.sections, key=lambda s: s.position)
    ]
    return OutlineRevision(
        outline_id=outline.id,
        captured_at=datetime.now(timezone.utc),
        sections_json=json.dumps(payload),
    )


def apply_outline_edit(session, outline: Outline, raw_sections: list[dict]) -> Outline:
    """Replace ``outline``'s sections with a human reviewer's edit.

    ``raw_sections`` is the full desired ordered list of ``{"title",
    "description"}`` objects — reordering, renaming, adding, and removing
    sections are all expressed as "here is the new full list" rather than
    per-section patch ops, so position is always derived from list order and
    can't drift out of sync.

    Raises :class:`OutlineEditError` if the proposed sections aren't a
    non-empty, well-formed list (same validation as a freshly generated
    outline). Snapshots the outline's current sections into an
    :class:`OutlineRevision` *before* mutating them, and — because an edit can
    invalidate what a reviewer already signed off on — resets ``status`` back
    to ``draft`` if the outline had been approved or rejected, requiring a
    fresh explicit approval.
    """
    new_sections = _validate_sections(raw_sections, error=OutlineEditError, source="outline edit")

    session.add(_snapshot_sections(outline))
    outline.sections = [
        OutlineSection(position=s.position, title=s.title, description=s.description)
        for s in new_sections
    ]
    outline.updated_at = datetime.now(timezone.utc)
    if outline.status != STATUS_DRAFT:
        outline.status = STATUS_DRAFT
        outline.reviewed_at = None
    return outline


def approve_outline(outline: Outline) -> Outline:
    """Mark ``outline`` as approved by a human reviewer.

    This is the explicit gate Phase 2 (section drafting) checks via
    :func:`require_outline_approved` before it may start.
    """
    outline.status = STATUS_APPROVED
    outline.reviewed_at = datetime.now(timezone.utc)
    return outline


def reject_outline(outline: Outline) -> Outline:
    """Mark ``outline`` as rejected by a human reviewer (needs more edits)."""
    outline.status = STATUS_REJECTED
    outline.reviewed_at = datetime.now(timezone.utc)
    return outline


def require_outline_approved(outline: Outline) -> None:
    """Gate for any downstream step (Phase 2 drafting) that must not run on
    an outline a human hasn't explicitly approved.

    Raises :class:`OutlineNotApprovedError` unless ``outline.status ==
    "approved"`` — covers both a never-reviewed outline (``draft``) and one a
    reviewer rejected, as well as one edited after approval and thus reset
    back to ``draft`` by :func:`apply_outline_edit`.
    """
    if outline.status != STATUS_APPROVED:
        raise OutlineNotApprovedError(
            f"outline {outline.id} is not approved (status={outline.status!r}); "
            "cannot proceed to drafting"
        )


class OpenAIOutlineClient:
    """Real client: one OpenAI chat-completion call constrained to JSON output.

    Not exercised against the live API by the test suite (no network access,
    no API key committed) — a ``client`` object can be injected in its place,
    the same convention used by other LLM-backed clients in this portfolio
    (e.g. ``OpenAIClassifierClient`` in ``ai-email-agent``).
    """

    def __init__(self, api_key: str, *, model: str | None = None, client: object | None = None) -> None:
        self._model = model or os.environ.get("OUTLINE_MODEL", DEFAULT_MODEL)
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI  # imported lazily: unused (and unneeded) in tests

            self._client = OpenAI(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, requirement_text: str) -> list[dict]:
        schema = {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["title", "description"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["sections"],
            "additionalProperties": False,
        }

        prompt = load_prompt("outline")
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": requirement_text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "rfp_outline", "schema": schema, "strict": True},
            },
        )
        content = response.choices[0].message.content
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise OutlineGenerationError(f"outline generator returned non-JSON content: {content!r}") from exc
        try:
            return payload["sections"]
        except (KeyError, TypeError) as exc:
            raise OutlineGenerationError(f"outline generator response missing 'sections': {payload!r}") from exc
