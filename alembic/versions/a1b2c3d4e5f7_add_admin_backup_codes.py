"""add backup_codes to admins for 2FA recovery

Revision ID: a1b2c3d4e5f7
Revises: f1a2b3c4d5e6
Create Date: 2026-05-04 01:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'admins' AND column_name = 'backup_codes'
    """))
    if not result.fetchone():
        op.add_column('admins', sa.Column('backup_codes', sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column('admins', 'backup_codes')
