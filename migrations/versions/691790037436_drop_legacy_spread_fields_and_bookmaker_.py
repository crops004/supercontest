"""Drop legacy spread fields and bookmaker_spreads

Revision ID: 691790037436
Revises: 2c667c57e246
Create Date: 2025-08-14 15:51:30.237614

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '691790037436'
down_revision = '2c667c57e246'
branch_labels = None
depends_on = None


def upgrade():
    # Drop columns if they exist (Alembic op.drop_column has no checkfirst, so use raw SQL)
    op.execute("ALTER TABLE game DROP COLUMN IF EXISTS spread;")
    op.execute("ALTER TABLE game DROP COLUMN IF EXISTS spread_source;")
    op.execute("ALTER TABLE game DROP COLUMN IF EXISTS spread_bookmaker_key;")

    # If you created this table earlier and don't need it anymore:
    op.execute("DROP TABLE IF EXISTS bookmaker_spreads;")

    # If you added the optional trigger earlier, drop it too (safe no-op otherwise)
    op.execute("DROP TRIGGER IF EXISTS trg_game_sync_legacy_spread ON game;")
    op.execute("DROP FUNCTION IF EXISTS game_sync_legacy_spread();")


def downgrade():
    # Recreate columns in case you ever downgrade
    op.add_column("game", sa.Column("spread", sa.Numeric(), nullable=True))
    op.add_column("game", sa.Column("spread_source", sa.Text(), nullable=True))
    op.add_column("game", sa.Column("spread_bookmaker_key", sa.Text(), nullable=True))
    # (Not recreating bookmaker_spreads—add it back only if needed)
