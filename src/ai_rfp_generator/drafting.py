"""Grounded section drafting from extracted facts.

Retrieval is deliberately deterministic for the first implementation: facts are
ranked by token overlap with the outline section title and description. The LLM
only receives retrieved facts and is instructed to cite them as [F<id>].
Semantic/hybrid retrieval can replace the ranker later without changing the
drafting interface.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from ai_rfp_generator.db import DraftSection, Fact, OutlineSection
from ai_rfp_generator.outline import require_outline_approved
from ai_rfp_generator.prompts import load_prompt

DEFAULT_MODEL = "gpt-4o-mini"
STRATEGY_INSTRUCTIONS = {
    "concise": "Be concise and direct. Prefer short paragraphs and omit repetition.",
    "detailed": "Provide a detailed response that explains the evidence and its relevance.",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_CITATION_RE = re.compile(r"\[F(\d+)\]")


class SectionDraftingError(RuntimeError):
    """Draft generation failed or returned an untrustworthy response."""


class SectionDraftingClient(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate(
        self,
        *,
        section_title: str,
        section_description: str,
        facts: list[Fact],
        strategy: str,
    ) -> str: ...


@dataclass(frozen=True)
class DraftResult:
    content: str
    fact_ids: tuple[int, ...]
    model: str


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text) if len(token) > 2}


def retrieve_relevant_facts(section: OutlineSection, facts: list[Fact], *, limit: int = 8) -> list[Fact]:
    """Rank non-duplicate facts by lexical overlap with the section."""
    if limit <= 0:
        return []
    query = _tokens(f"{section.title} {section.description}")
    ranked: list[tuple[int, int, Fact]] = []
    for fact in facts:
        if fact.is_duplicate:
            continue
        overlap = len(query & _tokens(fact.text))
        ranked.append((overlap, -fact.id, fact))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    positive = [fact for score, _, fact in ranked if score > 0]
    if positive:
        return positive[:limit]
    return [fact for _, _, fact in ranked[:limit]]


def _validate_citations(content: str, allowed_fact_ids: set[int]) -> None:
    cited = {int(match) for match in _CITATION_RE.findall(content)}
    unknown = cited - allowed_fact_ids
    if unknown:
        raise SectionDraftingError(f"draft cited facts that were not provided: {sorted(unknown)}")
    if allowed_fact_ids and not cited:
        raise SectionDraftingError("draft contains no fact citations")


def generate_section_draft(
    client: SectionDraftingClient,
    section: OutlineSection,
    facts: list[Fact],
    *,
    max_facts: int = 8,
    strategy: str = "detailed",
) -> DraftResult:
    require_outline_approved(section.outline)
    prompt = load_prompt("drafting")
    strategies = prompt.get("strategies", STRATEGY_INSTRUCTIONS)
    if strategy not in strategies:
        raise SectionDraftingError(
            f"unknown drafting strategy {strategy!r}; choose from {sorted(strategies)}"
        )

    relevant = retrieve_relevant_facts(section, facts, limit=max_facts)
    if not relevant:
        raise SectionDraftingError("no extracted facts are available for grounded drafting")

    content = client.generate(
        section_title=section.title,
        section_description=section.description,
        facts=relevant,
        strategy=strategy,
    ).strip()
    if not content:
        raise SectionDraftingError("section generator returned empty content")
    allowed = {fact.id for fact in relevant}
    _validate_citations(content, allowed)
    return DraftResult(content=content, fact_ids=tuple(fact.id for fact in relevant), model=client.model_name)


def persist_section_draft(
    session,
    section: OutlineSection,
    result: DraftResult,
    *,
    strategy: str = "grounded",
) -> DraftSection:
    draft = DraftSection(
        requirement_id=section.outline.requirement_id,
        outline_section_id=section.id,
        strategy=strategy,
        model=result.model,
        content=result.content,
        fact_ids_json=json.dumps(list(result.fact_ids)),
        generated_at=datetime.now(timezone.utc),
    )
    session.add(draft)
    return draft


class OpenAISectionDraftingClient:
    def __init__(self, api_key: str, *, model: str | None = None, client: object | None = None) -> None:
        self._model = model or os.environ.get("SECTION_DRAFT_MODEL", DEFAULT_MODEL)
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(
        self,
        *,
        section_title: str,
        section_description: str,
        facts: list[Fact],
        strategy: str,
    ) -> str:
        evidence = "\n".join(f"[F{fact.id}] {fact.text}" for fact in facts)
        prompt = load_prompt("drafting")
        strategies = prompt.get("strategies", STRATEGY_INSTRUCTIONS)
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": prompt["system"] + " " + strategies[strategy],
                },
                {
                    "role": "user",
                    "content": (
                        f"Section title: {section_title}\n"
                        f"Purpose: {section_description}\n\n"
                        f"Approved evidence:\n{evidence}"
                    ),
                },
            ],
        )
        return response.choices[0].message.content or ""
