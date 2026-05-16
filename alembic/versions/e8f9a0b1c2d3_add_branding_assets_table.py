"""add branding assets table

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-05-16 09:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def _index_exists(conn, index_name: str) -> bool:
    result = conn.execute(
        sa.text(
            """
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = :index_name
            """
        ),
        {"index_name": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "branding_assets"):
        op.create_table(
            "branding_assets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("mime_type", sa.String(length=128), nullable=False),
            sa.Column("data", sa.LargeBinary(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("key", name="uq_branding_assets_key"),
        )

    if not _index_exists(conn, "ix_branding_assets_key"):
        op.create_index("ix_branding_assets_key", "branding_assets", ["key"], unique=True)


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "ix_branding_assets_key"):
        op.drop_index("ix_branding_assets_key", table_name="branding_assets")
    if _table_exists(conn, "branding_assets"):
        op.drop_table("branding_assets")
