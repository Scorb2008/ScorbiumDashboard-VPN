"""add blacklisted tokens table

Revision ID: c6d7e8f9a0b1
Revises: b2c3d4e5f6a7
Create Date: 2026-05-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = :table_name
    """), {"table_name": table_name})
    return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :table_name
          AND column_name = :column_name
    """), {"table_name": table_name, "column_name": column_name})
    return result.fetchone() is not None


def _index_exists(conn, index_name: str) -> bool:
    result = conn.execute(sa.text("""
        SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = :index_name
    """), {"index_name": index_name})
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, 'blacklisted_tokens'):
        op.create_table(
            'blacklisted_tokens',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('jti', sa.String(length=128), nullable=True),
            sa.Column('sub', sa.String(length=64), nullable=False),
            sa.Column('blacklist_all', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        )
    else:
        if not _column_exists(conn, 'blacklisted_tokens', 'jti'):
            op.add_column('blacklisted_tokens', sa.Column('jti', sa.String(length=128), nullable=True))
        if not _column_exists(conn, 'blacklisted_tokens', 'sub'):
            op.add_column('blacklisted_tokens', sa.Column('sub', sa.String(length=64), nullable=False, server_default=''))
        if not _column_exists(conn, 'blacklisted_tokens', 'blacklist_all'):
            op.add_column(
                'blacklisted_tokens',
                sa.Column('blacklist_all', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            )
        if not _column_exists(conn, 'blacklisted_tokens', 'expires_at'):
            op.add_column('blacklisted_tokens', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
        if not _column_exists(conn, 'blacklisted_tokens', 'created_at'):
            op.add_column(
                'blacklisted_tokens',
                sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            )
        if not _column_exists(conn, 'blacklisted_tokens', 'updated_at'):
            op.add_column(
                'blacklisted_tokens',
                sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
            )

    if not _index_exists(conn, 'ix_blacklisted_tokens_jti'):
        op.create_index('ix_blacklisted_tokens_jti', 'blacklisted_tokens', ['jti'])
    if not _index_exists(conn, 'ix_blacklisted_tokens_sub'):
        op.create_index('ix_blacklisted_tokens_sub', 'blacklisted_tokens', ['sub'])


def downgrade() -> None:
    conn = op.get_bind()

    if _index_exists(conn, 'ix_blacklisted_tokens_jti'):
        op.drop_index('ix_blacklisted_tokens_jti', table_name='blacklisted_tokens')
    if _index_exists(conn, 'ix_blacklisted_tokens_sub'):
        op.drop_index('ix_blacklisted_tokens_sub', table_name='blacklisted_tokens')
    if _table_exists(conn, 'blacklisted_tokens'):
        op.drop_table('blacklisted_tokens')
