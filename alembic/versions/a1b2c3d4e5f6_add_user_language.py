"""add user language

Revision ID: a1b2c3d4e5f6
Revises: 4d5f8377eff0
Create Date: 2026-04-02 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '4d5f8377eff0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    table_exists = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'users'
    """)).fetchone()
    if not table_exists:
        return
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'language'
    """))
    if not result.fetchone():
        op.add_column('users', sa.Column('language', sa.String(8), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    table_exists = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'users'
    """)).fetchone()
    if not table_exists:
        return
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'language'
    """))
    if result.fetchone():
        op.drop_column('users', 'language')
