"""add admin features: last_seen, audit_log

Revision ID: e6f7a8b9c0d1
Revises: c5d6e7f8a9b0
Create Date: 2026-05-03 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'last_seen'
    """))
    if not result.fetchone():
        op.add_column('users', sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True))

    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.tables WHERE table_name = 'audit_log'
    """))
    if not result.fetchone():
        op.create_table(
            'audit_log',
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('admin_id', sa.BigInteger, nullable=False, index=True),
            sa.Column('action', sa.String(64), nullable=False, index=True),
            sa.Column('target_type', sa.String(32), nullable=True),
            sa.Column('target_id', sa.BigInteger, nullable=True),
            sa.Column('details', sa.Text, nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=True),
        )

    # Handle case where table exists but updated_at column is missing
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'audit_log' AND column_name = 'updated_at'
    """))
    if not result.fetchone():
        op.add_column('audit_log', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True))


def downgrade() -> None:
    op.drop_table('audit_log')
    op.drop_column('users', 'last_seen')
