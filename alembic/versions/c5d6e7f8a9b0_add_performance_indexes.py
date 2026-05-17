"""add performance indexes

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    indexes = [
        ("ix_vpn_keys_user_id", "vpn_keys", "user_id"),
        ("ix_vpn_keys_expires_at", "vpn_keys", "expires_at"),
        ("ix_vpn_keys_status", "vpn_keys", "status"),
        ("ix_payments_user_id", "payments", "user_id"),
        ("ix_payments_status", "payments", "status"),
    ]
    for idx_name, table, column in indexes:
        result = conn.execute(sa.text("""
            SELECT 1 FROM pg_indexes WHERE indexname = :idx
        """), {"idx": idx_name})
        if not result.fetchone():
            op.create_index(idx_name, table, [column])


def downgrade() -> None:
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_index("ix_vpn_keys_status", table_name="vpn_keys")
    op.drop_index("ix_vpn_keys_expires_at", table_name="vpn_keys")
    op.drop_index("ix_vpn_keys_user_id", table_name="vpn_keys")
