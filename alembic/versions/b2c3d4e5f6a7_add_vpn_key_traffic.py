"""add vpn_key traffic (download/upload)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-05-09 16:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vpn_keys' AND column_name = 'download'
    """))
    if not result.fetchone():
        op.add_column('vpn_keys', sa.Column('download', sa.BigInteger(), nullable=False, server_default='0'))
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vpn_keys' AND column_name = 'upload'
    """))
    if not result.fetchone():
        op.add_column('vpn_keys', sa.Column('upload', sa.BigInteger(), nullable=False, server_default='0'))


def downgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vpn_keys' AND column_name = 'download'
    """))
    if result.fetchone():
        op.drop_column('vpn_keys', 'download')
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vpn_keys' AND column_name = 'upload'
    """))
    if result.fetchone():
        op.drop_column('vpn_keys', 'upload')
