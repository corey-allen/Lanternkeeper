"""light timing settings and the real-time play clock

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('campaign', sa.Column('light_timing', sa.String(length=16), nullable=True))
    op.add_column('campaign', sa.Column('light_multiple', sa.String(length=16), nullable=True))
    op.add_column('campaign', sa.Column('idle_pause_seconds', sa.Integer(), server_default='300', nullable=False))
    op.create_check_constraint('ck_campaign_idle_pause', 'campaign', 'idle_pause_seconds BETWEEN 30 AND 3600')
    op.add_column('world_clock', sa.Column('realtime_last_tick_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('world_clock', sa.Column('realtime_pending_seconds', sa.Integer(), server_default='0', nullable=False))
    op.create_check_constraint('ck_clock_pending', 'world_clock', 'realtime_pending_seconds >= 0')


def downgrade() -> None:
    op.drop_constraint('ck_clock_pending', 'world_clock', type_='check')
    op.drop_column('world_clock', 'realtime_pending_seconds')
    op.drop_column('world_clock', 'realtime_last_tick_at')
    op.drop_constraint('ck_campaign_idle_pause', 'campaign', type_='check')
    op.drop_column('campaign', 'idle_pause_seconds')
    op.drop_column('campaign', 'light_multiple')
    op.drop_column('campaign', 'light_timing')
