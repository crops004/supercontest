"""Add odds_event_id and kickoff_at to Game

Revision ID: 4ac2fb6f8b9c
Revises: e3e6f7d709d1
Create Date: 2025-08-14 10:35:06.257552

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4ac2fb6f8b9c'
down_revision = 'e3e6f7d709d1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('game', sa.Column('odds_event_id', sa.Text(), nullable=True))
    op.add_column('game', sa.Column('kickoff_at', sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint('uq_game_odds_event_id', 'game', ['odds_event_id'])


def downgrade():
    op.drop_constraint('uq_game_odds_event_id', 'game', type_='unique')
    op.drop_column('game', 'kickoff_at')
    op.drop_column('game', 'odds_event_id')
