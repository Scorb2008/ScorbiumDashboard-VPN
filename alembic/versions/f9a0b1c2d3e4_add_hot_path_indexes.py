"""add hot path indexes

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-05-25 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    indexes = [
        (
            "ix_vpn_keys_user_status_created_at",
            "vpn_keys",
            ["user_id", "status", "created_at"],
        ),
        ("ix_vpn_keys_user_created_at", "vpn_keys", ["user_id", "created_at"]),
        ("ix_payments_user_created_at", "payments", ["user_id", "created_at"]),
        (
            "ix_payments_status_provider_created_at",
            "payments",
            ["status", "provider", "created_at"],
        ),
    ]

    for index_name, table_name, columns in indexes:
        if not _index_exists(conn, index_name):
            op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    for index_name, table_name in (
        ("ix_payments_status_provider_created_at", "payments"),
        ("ix_payments_user_created_at", "payments"),
        ("ix_vpn_keys_user_created_at", "vpn_keys"),
        ("ix_vpn_keys_user_status_created_at", "vpn_keys"),
    ):
        op.drop_index(index_name, table_name=table_name)
