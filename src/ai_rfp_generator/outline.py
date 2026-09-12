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

from ai_rfp_generator.db import Outline, OutlineSection, Requirement, RequirementItem

DEFAULT_MODEL = "gpt-4o-mini"


class OutlineGenerationError(RuntimeError):
    """The outline-generation call failed, or its response can't be trusted."""


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


def generate_outline(client: OutlineGeneratorClient, items: list[RequirementItem]) -> OutlineDraft:
    """Generate and validate an outline for ``items``.

    Raises :class:`OutlineGenerationError` if there are no items to outline,
    or if the client's response isn't a non-empty ordered list of sections
    each with a non-blank title and description.
    """
    if not items:
        raise OutlineGenerationError("cannot generate an outline from zero requirement items")

    raw_sections = client.generate(_build_requirement_text(items))
    if not isinstance(raw_sections, list) or not raw_sections:
        raise OutlineGenerationError(f"outline generator returned no sections (got {raw_sections!r})")

    sections: list[OutlineSectionDraft] = []
    for index, entry in enumerate(raw_sections):
        if not isinstance(entry, dict):
            raise OutlineGenerationError(f"section {index} is not an object: {entry!r}")
        title, description = entry.get("title"), entry.get("description")
        if not isinstance(title, str) or not title.strip():
            raise OutlineGenerationError(f"section {index} has an invalid title: {title!r}")
        if not isinstance(description, str) or not description.strip():
            raise OutlineGenerationError(f"section {index} has an invalid description: {description!r}")
        sections.append(OutlineSectionDraft(position=index, title=title.strip(), description=description.strip()))

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
    )
    outline.sections = [
        OutlineSection(position=s.position, title=s.title, description=s.description)
        for s in draft.sections
    ]
    session.add(outline)
    return outline


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

        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are drafting the outline for an RFP response. Given the "
                        "normalized requirement items below, propose an ordered list "
                        "of response sections, each with a short title and a one- to "
                        "two-sentence description of what it should cover."
                    ),
                },
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
