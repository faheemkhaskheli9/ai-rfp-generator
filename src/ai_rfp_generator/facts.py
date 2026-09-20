"""Extract discrete, cited facts from source materials (Phase 2).

Fact extraction here is deterministic sentence-boundary splitting, not an
LLM call: unlike ``outline.py``'s document-outline generation — where the
LLM's judgment is the whole point, grouping/naming sections that don't exist
verbatim in the input — a fact record's value is that its ``start_offset``/
``end_offset`` addresses an exact, verifiable substring of
``SourceMaterial.extracted_text``. An LLM asked to "extract the facts" would
paraphrase or reorder them, and a paraphrase can't be located back in the
source text by character offset, which would break the citation guarantee
issue #6's acceptance criteria require ("a correct source reference"). A
cheap rule-based pass over already-extracted plain text (``source_materials.py``
does the parsing/chunking-adjacent work of turning uploads into
``extracted_text``; this module doesn't re-parse the raw file) is enough to
split prose into candidate claims, mirroring ``normalize.py``'s reasoning for
using a deterministic pass on short, unstructured intake text.

Deduplication is dependency-light on purpose (see ``requirements.txt`` — no
embedding/vector-search library is pinned yet, and pulling one in just for
this would be a new heavy dependency for a single feature): exact duplicates
are caught by comparing whitespace/case-normalized text, and near-duplicates
by ``difflib.SequenceMatcher`` (stdlib) similarity ratio. Matches are flagged
via ``Fact.is_duplicate``/``duplicate_of`` rather than dropped, so a
duplicate fact never loses its own citation back to the document/passage it
came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from ai_rfp_generator.db import Fact, SourceMaterial, now_utc

#: A candidate sentence shorter than this (after stripping) is treated as a
#: fragment (a bullet marker, a lone heading word) rather than a standalone
#: claim worth citing on its own.
MIN_FACT_CHARS = 15

#: difflib.SequenceMatcher ratio at/above which two normalized fact texts are
#: considered near-duplicates. Chosen to catch light rewording ("We delivered
#: the portal in 2023." vs "We delivered the portal in 2023 for the client.")
#: while not conflating two genuinely different sentences that merely share
#: common words.
NEAR_DUPLICATE_THRESHOLD = 0.85

# Split on whitespace that immediately follows a sentence-ending punctuation
# mark. Runs of non-whitespace before that boundary stay intact, so each
# piece returned by re.split() is a contiguous substring of the original
# text -- required so _sentence_spans() below can relocate each one by a
# forward-only text.index() scan and recover its exact offsets.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FactCandidate:
    text: str
    start_offset: int
    end_offset: int


def normalize_fact_text(text: str) -> str:
    """Collapse whitespace and lowercase, for exact-duplicate comparison."""
    return _WHITESPACE_RE.sub(" ", text.strip()).lower()


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    """Split ``text`` into ``(start_offset, end_offset, sentence)`` triples.

    Offsets are located by scanning forward from the end of the previous
    match (never restarting from 0), so two identical sentences appearing
    twice in the same document each get their own correct, distinct offset
    range rather than both resolving to the first occurrence.
    """
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for raw_piece in _SENTENCE_BOUNDARY_RE.split(text):
        piece = raw_piece.strip()
        if not piece:
            continue
        start = text.index(piece, cursor)
        end = start + len(piece)
        spans.append((start, end, piece))
        cursor = end
    return spans


def extract_fact_candidates(source_material: SourceMaterial) -> list[FactCandidate]:
    """Split ``source_material.extracted_text`` into discrete fact candidates.

    Candidates shorter than :data:`MIN_FACT_CHARS` are dropped as fragments,
    not persisted at all (there is nothing to cite or de-duplicate).
    """
    return [
        FactCandidate(text=sentence, start_offset=start, end_offset=end)
        for start, end, sentence in _sentence_spans(source_material.extracted_text)
        if len(sentence) >= MIN_FACT_CHARS
    ]


def find_duplicate(normalized_text: str, existing_facts: list[Fact]) -> Fact | None:
    """Return the existing canonical :class:`Fact` that ``normalized_text``
    duplicates, or ``None`` if it's a genuinely new fact.

    An exact normalized-text match always wins over a near-duplicate match.
    The returned fact is always a *root* (its own ``duplicate_of`` is
    ``None``) even if the matched record was itself flagged as a duplicate,
    so ``duplicate_of`` chains never nest more than one level deep.
    """

    def _root(fact: Fact) -> Fact:
        return fact.duplicate_of if fact.duplicate_of is not None else fact

    for existing in existing_facts:
        if existing.normalized_text == normalized_text:
            return _root(existing)

    best: Fact | None = None
    best_score = 0.0
    for existing in existing_facts:
        score = SequenceMatcher(None, normalized_text, existing.normalized_text).ratio()
        if score >= NEAR_DUPLICATE_THRESHOLD and score > best_score:
            best, best_score = existing, score
    return _root(best) if best is not None else None


def extract_and_persist_facts(session, source_material: SourceMaterial) -> list[Fact]:
    """Extract fact candidates from ``source_material`` and persist them.

    De-duplication is scoped to the owning requirement (``requirement_id``)
    across *all* of its source materials, not just this one, so a claim
    repeated across two uploaded documents (or twice within the same one) is
    flagged against whichever record was seen first rather than persisted as
    two unrelated facts. Does not commit — callers control the transaction
    boundary, matching ``outline.persist_outline``/``source_materials.store_source_materials``.
    """
    existing_facts: list[Fact] = list(
        session.query(Fact).filter(Fact.requirement_id == source_material.requirement_id).all()
    )

    persisted: list[Fact] = []
    for candidate in extract_fact_candidates(source_material):
        normalized = normalize_fact_text(candidate.text)
        duplicate_of = find_duplicate(normalized, existing_facts)

        fact = Fact(
            requirement_id=source_material.requirement_id,
            source_material_id=source_material.id,
            text=candidate.text,
            normalized_text=normalized,
            start_offset=candidate.start_offset,
            end_offset=candidate.end_offset,
            is_duplicate=duplicate_of is not None,
            duplicate_of=duplicate_of,
            extracted_at=now_utc(),
        )
        session.add(fact)
        # So a fact later in this same batch can be de-duplicated against
        # one just added, not only against facts already committed from a
        # prior call.
        existing_facts.append(fact)
        persisted.append(fact)

    return persisted
