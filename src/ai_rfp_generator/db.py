"""Persistence for ingested requirements.

Production target is PostgreSQL (README §3); the boundary is a SQLAlchemy
engine URL read from ``DATABASE_URL``, defaulting to a local SQLite file so the
API and its tests run with zero external services. Swapping to Postgres is a
connection-string change, not a code change.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="received")
    content: Mapped[str] = mapped_column(Text)

    items: Mapped[list["RequirementItem"]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan", order_by="RequirementItem.position"
    )
    outlines: Mapped[list["Outline"]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan"
    )
    source_materials: Mapped[list["SourceMaterial"]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan"
    )


class RequirementItem(Base):
    """A single normalized item (section/question/deadline/requirement) parsed
    out of a :class:`Requirement`'s raw ``content``.
    """

    __tablename__ = "requirement_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer)
    item_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)

    requirement: Mapped[Requirement] = relationship(back_populates="items")


class Outline(Base):
    """An LLM-generated document outline for one :class:`Requirement`.

    A requirement can be re-outlined (e.g. after edits or a re-run with a
    different model) — each attempt is its own row, linked by
    ``requirement_id``, rather than overwriting a prior outline.

    ``status`` is the review/approval state gating Phase 2 drafting (see
    ``outline.require_outline_approved``): ``draft`` (freshly generated, or
    edited since last approval) -> ``approved`` or ``rejected`` by a human
    reviewer. Editing an outline that was already approved/rejected resets it
    back to ``draft`` so a stale approval can never silently cover changed
    content.
    """

    __tablename__ = "outlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(128))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requirement: Mapped[Requirement] = relationship(back_populates="outlines")
    sections: Mapped[list["OutlineSection"]] = relationship(
        back_populates="outline", cascade="all, delete-orphan", order_by="OutlineSection.position"
    )
    revisions: Mapped[list["OutlineRevision"]] = relationship(
        back_populates="outline", cascade="all, delete-orphan", order_by="OutlineRevision.captured_at"
    )


class OutlineSection(Base):
    __tablename__ = "outline_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outline_id: Mapped[int] = mapped_column(ForeignKey("outlines.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)

    outline: Mapped[Outline] = relationship(back_populates="sections")


class OutlineRevision(Base):
    """A frozen snapshot of an outline's sections captured just before an edit.

    Taken every time ``outline.apply_outline_edit`` mutates ``Outline.sections``,
    so the original LLM-generated outline (and every edit since) stays
    recoverable even though ``OutlineSection`` rows themselves are mutated in
    place. ``sections_json`` holds a ``[{"position", "title", "description"}, ...]``
    list.
    """

    __tablename__ = "outline_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outline_id: Mapped[int] = mapped_column(ForeignKey("outlines.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sections_json: Mapped[str] = mapped_column(Text)

    outline: Mapped[Outline] = relationship(back_populates="revisions")


class SourceMaterial(Base):
    """An uploaded source document (past proposal, case study, capability
    statement) linked to the :class:`Requirement` it will support fact
    extraction for (Phase 2, see ``source_materials.store_source_materials``).

    Content-addressed by ``content_hash`` (sha256 of the raw file bytes): the
    stored file on disk is named after the hash, so re-uploading identical
    content for the same requirement is idempotent instead of creating a
    duplicate stored copy or row — the ``UniqueConstraint`` below enforces
    that at the database level too. ``extracted_text`` is stored alongside
    the raw file (same extraction used for requirement intake) so the later
    fact-extraction step doesn't need to reparse the original document.
    """

    __tablename__ = "source_materials"
    __table_args__ = (UniqueConstraint("requirement_id", "content_hash", name="uq_source_material_dedup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    extension: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(Integer)
    extracted_text: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    requirement: Mapped[Requirement] = relationship(back_populates="source_materials")


def make_engine(database_url: str | None = None):
    database_url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./rfp.db")
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
