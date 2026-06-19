"""Initial ACTA SQLite schema.

Revision ID: 20260619_0001
Revises:
Create Date: 2026-06-19 12:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260619_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("content_enc", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Text(), nullable=False),
        sa.Column("tags", sa.Text(), nullable=False),
        sa.Column("metadata", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_mem_kind", "memories", ["kind", "user_id"], unique=False)
    op.create_table(
        "personal",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value_enc", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
        USING fts5(memory_id UNINDEXED, user_id UNINDEXED, kind UNINDEXED, tokens)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memories_fts")
    op.drop_table("personal")
    op.drop_index("idx_mem_kind", table_name="memories")
    op.drop_table("memories")
