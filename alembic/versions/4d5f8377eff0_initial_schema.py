"""initial_schema

Revision ID: 4d5f8377eff0
Revises: 
Create Date: 2026-03-24 13:03:56.496080

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4d5f8377eff0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table('users',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('username', sa.String(64), nullable=True),
        sa.Column('full_name', sa.String(256), nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('is_banned', sa.Boolean(), default=False, nullable=False),
        sa.Column('balance', sa.Numeric(10, 2), default=0, nullable=False),
        sa.Column('referral_code', sa.String(32), nullable=True, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── plans ──────────────────────────────────────────────────────────────
    op.create_table('plans',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('slug', sa.String(64), nullable=False, unique=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('duration_days', sa.Integer(), nullable=False),
        sa.Column('price', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(8), default='RUB', nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('sort_order', sa.Integer(), default=0, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── vpn_keys (= подписки) ──────────────────────────────────────────────
    op.create_table('vpn_keys',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_id', sa.Integer(), sa.ForeignKey('plans.id', ondelete='SET NULL'), nullable=True),
        sa.Column('pasarguard_key_id', sa.String(128), nullable=True, unique=True),
        sa.Column('access_url', sa.Text(), nullable=False),
        sa.Column('name', sa.String(128), nullable=True),
        sa.Column('price', sa.Numeric(10, 2), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(16), default='active', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── payments ───────────────────────────────────────────────────────────
    op.create_table('payments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('vpn_key_id', sa.Integer(), sa.ForeignKey('vpn_keys.id', ondelete='SET NULL'), nullable=True),
        sa.Column('provider', sa.String(32), nullable=False),
        sa.Column('external_id', sa.String(256), nullable=True, unique=True),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(8), default='RUB', nullable=False),
        sa.Column('status', sa.String(16), default='pending', nullable=False),
        sa.Column('meta', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── promo_codes ────────────────────────────────────────────────────────
    op.create_table('promo_codes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('code', sa.String(64), nullable=False, unique=True),
        sa.Column('promo_type', sa.String(32), nullable=False),
        sa.Column('value', sa.Numeric(10, 2), nullable=False),
        sa.Column('plan_id', sa.Integer(), sa.ForeignKey('plans.id', ondelete='SET NULL'), nullable=True),
        sa.Column('max_uses', sa.Integer(), default=0, nullable=False),
        sa.Column('current_uses', sa.Integer(), default=0, nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── referrals ──────────────────────────────────────────────────────────
    op.create_table('referrals',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('referrer_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('referred_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('bonus_type', sa.String(32), nullable=True),
        sa.Column('bonus_value', sa.Numeric(10, 2), nullable=True),
        sa.Column('is_paid', sa.Boolean(), default=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── support_tickets ────────────────────────────────────────────────────
    op.create_table('support_tickets',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('subject', sa.String(256), nullable=False),
        sa.Column('status', sa.String(32), default='open', nullable=False),
        sa.Column('priority', sa.String(32), default='normal', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── ticket_messages ────────────────────────────────────────────────────
    op.create_table('ticket_messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('support_tickets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sender_id', sa.BigInteger(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('is_admin', sa.Boolean(), default=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── broadcasts ─────────────────────────────────────────────────────────
    op.create_table('broadcasts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('title', sa.String(256), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('target', sa.String(32), default='all', nullable=False),
        sa.Column('parse_mode', sa.String(16), default='HTML', nullable=False),
        sa.Column('status', sa.String(32), default='draft', nullable=False),
        sa.Column('sent_count', sa.Integer(), default=0, nullable=False),
        sa.Column('failed_count', sa.Integer(), default=0, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── bot_settings ───────────────────────────────────────────────────────
    op.create_table('bot_settings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('key', sa.String(128), nullable=False, unique=True),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('bot_settings')
    op.drop_table('broadcasts')
    op.drop_table('ticket_messages')
    op.drop_table('support_tickets')
    op.drop_table('referrals')
    op.drop_table('promo_codes')
    op.drop_table('payments')
    op.drop_table('vpn_keys')
    op.drop_table('plans')
    op.drop_table('users')
