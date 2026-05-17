"""add totp_secret to admins for 2FA

Revision ID: f1a2b3c4d5e6
Revises: e6f7a8b9c0d1
Create Date: 2026-05-04 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'admins' AND column_name = 'totp_secret'
    """))
    if not result.fetchone():
        op.add_column('admins', sa.Column('totp_secret', sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column('admins', 'totp_secret')
