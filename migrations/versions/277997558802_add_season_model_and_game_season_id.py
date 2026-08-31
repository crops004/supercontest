"""add season model and game.season_id

Revision ID: 277997558802
Revises: e60b5cb6bb2f
Create Date: 2026-08-31 11:52:33.309789

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '277997558802'
down_revision = 'e60b5cb6bb2f'
branch_labels = None
depends_on = None


def upgrade():
    # Clean up two orphaned tables from an abandoned early design (never
    # created by any tracked migration, never referenced by any code) so the
    # new 'season' table below can take that name cleanly.
    op.execute("DROP TABLE IF EXISTS user_season")
    op.execute("DROP TABLE IF EXISTS season")

    op.create_table('season',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('week1_anchor', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default='false', nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('year')
    )

    # Seed the 2025 season with the same week-1 anchor the app previously
    # read from the NFL_WEEK1_TUESDAY env var (2025-09-02 00:00 Denver time,
    # which is MDT / UTC-6), and mark it current.
    op.execute(
        "INSERT INTO season (year, week1_anchor, is_current) "
        "VALUES (2025, '2025-09-02T00:00:00-06:00', true)"
    )

    with op.batch_alter_table('game', schema=None) as batch_op:
        batch_op.add_column(sa.Column('season_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_game_season_id'), ['season_id'], unique=False)
        batch_op.create_foreign_key('fk_game_season_id', 'season', ['season_id'], ['id'])

    # Backfill every existing game onto the 2025 season, then enforce NOT NULL.
    op.execute("UPDATE game SET season_id = (SELECT id FROM season WHERE year = 2025)")

    with op.batch_alter_table('game', schema=None) as batch_op:
        batch_op.alter_column('season_id', nullable=False)


def downgrade():
    with op.batch_alter_table('game', schema=None) as batch_op:
        batch_op.drop_constraint('fk_game_season_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_game_season_id'))
        batch_op.drop_column('season_id')

    op.drop_table('season')
