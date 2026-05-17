"""add promo usages table

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-05-14 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a0b1"
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


def _constraint_exists(conn, table_name: str, constraint_name: str) -> bool:
    result = conn.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.table_constraints
            WHERE table_schema = 'public'
              AND table_name = :table_name
              AND constraint_name = :constraint_name
            """
        ),
        {"table_name": table_name, "constraint_name": constraint_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "promo_usages"):
        op.create_table(
            "promo_usages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("promo_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["promo_id"], ["promo_codes.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )

    if not _constraint_exists(conn, "promo_usages", "uq_promo_user"):
        op.create_unique_constraint("uq_promo_user", "promo_usages", ["promo_id", "user_id"])

    if not _index_exists(conn, "ix_promo_usages_promo_id"):
        op.create_index("ix_promo_usages_promo_id", "promo_usages", ["promo_id"])
    if not _index_exists(conn, "ix_promo_usages_user_id"):
        op.create_index("ix_promo_usages_user_id", "promo_usages", ["user_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "ix_promo_usages_promo_id"):
        op.drop_index("ix_promo_usages_promo_id", table_name="promo_usages")
    if _index_exists(conn, "ix_promo_usages_user_id"):
        op.drop_index("ix_promo_usages_user_id", table_name="promo_usages")
    if _table_exists(conn, "promo_usages"):
        op.drop_table("promo_usages")
