"""add admins table

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-15 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create admins table if not exists
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'admins'
    """))
    if not result.fetchone():
        op.create_table(
            'admins',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('username', sa.String(64), unique=True, nullable=False, index=True),
            sa.Column('password_hash', sa.String(256), nullable=False),
            sa.Column('role', sa.String(32), nullable=False, server_default='operator'),
            sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        )

    # Add superadmin if not exists
    result = conn.execute(sa.text("""
        SELECT 1 FROM admins WHERE username = 'superadmin'
    """))
    if not result.fetchone():
        import os
        env_pass = os.environ.get("WEB_SUPERADMIN_PASSWORD", "changeme")
        try:
            from bcrypt import hashpw, gensalt
            pw_bytes = env_pass.encode("utf-8")[:72]
            pw_hash = hashpw(pw_bytes, gensalt(rounds=12)).decode("ascii")
        except Exception:
            # Fallback if bcrypt not available during migration
            pw_hash = env_pass
        conn.execute(sa.text("""
            INSERT INTO admins (username, password_hash, role, is_active, created_at, updated_at)
            VALUES ('superadmin', :pw_hash, 'superadmin', true, NOW(), NOW())
        """), {"pw_hash": pw_hash})


def downgrade() -> None:
    op.drop_table('admins')
