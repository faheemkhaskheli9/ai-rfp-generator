"""Adopt or create the current application schema.

Revision ID: 20260926_0001
Revises:
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from ai_rfp_generator.db import Base

revision = "20260926_0001"
down_revision = None
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    # Create every table that does not exist. This makes the baseline usable
    # for both a brand-new database and older deployments that already contain
    # part of the schema.
    Base.metadata.create_all(bind=op.get_bind())

    # create_all does not add columns to an existing table, so explicitly
    # adopt the draft-review fields introduced after the first prototype.
    draft_columns = _column_names("draft_sections")
    if "status" not in draft_columns:
        op.add_column(
            "draft_sections",
            sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        )
    if "reviewed_at" not in draft_columns:
        op.add_column(
            "draft_sections",
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    # This is an adoption baseline and may have been applied to a database that
    # contained pre-existing user data. Automatically dropping the adopted
    # schema would be unsafe, so downgrade is intentionally non-destructive.
    pass
