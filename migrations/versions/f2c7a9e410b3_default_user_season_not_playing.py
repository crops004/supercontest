"""default user_season.is_playing to false, driven by actual pick evidence

Revision ID: f2c7a9e410b3
Revises: a14f6d8c2b91
Create Date: 2026-09-01 15:20:00.000000

The previous migration bootstrapped every user as is_playing=true for every
season so nothing visibly changed. Going forward, a user only counts toward
a season once they submit a pick (app/services/roster.mark_user_playing),
so recompute is_playing here from real pick history and flip the column
default for any future rows created before a user's first pick.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2c7a9e410b3'
down_revision = 'a14f6d8c2b91'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE user_season us
        SET is_playing = EXISTS (
            SELECT 1 FROM pick p
            WHERE p.user_id = us.user_id AND p.season_id = us.season_id
        )
    """)
    op.execute("ALTER TABLE user_season ALTER COLUMN is_playing SET DEFAULT false")


def downgrade():
    op.execute("ALTER TABLE user_season ALTER COLUMN is_playing SET DEFAULT true")
    op.execute("UPDATE user_season SET is_playing = true")
