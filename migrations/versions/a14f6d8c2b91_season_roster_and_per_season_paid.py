"""add user_season.is_playing, move entry_paid off user onto user_season

Revision ID: a14f6d8c2b91
Revises: 922208256889
Create Date: 2026-09-01 14:05:00.000000

Backfills a user_season row for every existing (user, season) pair that
doesn't have one yet, so standings/history/trend can start scoping "who's
playing" to a per-season roster instead of every user in the users table.
is_playing defaults to true and entry_paid is copied from the old flat
user.entry_paid column, so nothing visibly changes until an admin adjusts
the new roster/paid toggles.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a14f6d8c2b91'
down_revision = '922208256889'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE user_season ADD COLUMN IF NOT EXISTS is_playing BOOLEAN NOT NULL DEFAULT true")

    # Backfill missing rows while user.entry_paid still exists, so its last
    # known value carries forward as this season's starting paid status.
    op.execute("""
        INSERT INTO user_season (user_id, season_id, is_playing, entry_paid, created_at)
        SELECT u.id, s.id, true, COALESCE(u.entry_paid, false), now()
        FROM "user" u
        CROSS JOIN season s
        WHERE NOT EXISTS (
            SELECT 1 FROM user_season us WHERE us.user_id = u.id AND us.season_id = s.id
        )
    """)

    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS entry_paid')


def downgrade():
    op.execute('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS entry_paid BOOLEAN DEFAULT false')

    # Best-effort: restore from the active season's roster (there's no way
    # to recover the original flat value once it's been dropped).
    op.execute("""
        UPDATE "user" u
        SET entry_paid = us.entry_paid
        FROM user_season us
        JOIN season s ON s.id = us.season_id
        WHERE us.user_id = u.id AND s.is_active
    """)

    with op.batch_alter_table('user_season', schema=None) as batch_op:
        batch_op.drop_column('is_playing')
