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
