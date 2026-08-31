"""add season model, season_id everywhere, user_season

Revision ID: 277997558802
Revises: e60b5cb6bb2f
Create Date: 2026-08-31 11:52:33.309789

This migration is written to be safe to run against two very different
starting states:

  1. A fresh database that only has the 19 previously-tracked migrations
     applied (e.g. a new local dev DB) - it creates everything from scratch.
  2. Production, which already has a fully-built season/season_id/user_season
     structure that was added directly to the database at some point outside
     of any tracked migration (discovered when this migration's first,
     naive version tried to DROP the "orphaned" season table and Postgres
     refused - it wasn't orphaned, it had real foreign keys and 278 games'
     worth of real data pointing at it). Every statement below is guarded
     (CREATE ... IF NOT EXISTS, or an explicit existence check) so it only
     creates what's actually missing in either environment.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '277997558802'
down_revision = 'e60b5cb6bb2f'
branch_labels = None
depends_on = None


def _add_constraint_if_missing(conname: str, table: str, definition: str) -> str:
    return f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{conname}') THEN
        ALTER TABLE {table} ADD CONSTRAINT {conname} {definition};
    END IF;
END $$;
"""


def upgrade():
    # ------------------------------------------------------------------
    # season
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS season (
            id SERIAL PRIMARY KEY,
            year INTEGER NOT NULL UNIQUE,
            name TEXT,
            start_date DATE,
            end_date DATE,
            is_active BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_season_one_active
        ON season (is_active) WHERE is_active
    """)

    # Seed 2025 (matches the previous NFL_WEEK1_TUESDAY env-var default) and
    # 2026 (the real Week 1 Tuesday anchor), without clobbering rows that
    # already exist.
    op.execute("""
        INSERT INTO season (year, name, start_date, is_active)
        VALUES (2025, '2025 NFL', '2025-09-02', false)
        ON CONFLICT (year) DO NOTHING
    """)
    op.execute("""
        INSERT INTO season (year, name, start_date, is_active)
        VALUES (2026, '2026 NFL', '2026-09-08', true)
        ON CONFLICT (year) DO NOTHING
    """)
    # Backfill only what's actually missing (e.g. production's 2026 row
    # already exists but has no start_date yet).
    op.execute("UPDATE season SET start_date = '2025-09-02' WHERE year = 2025 AND start_date IS NULL")
    op.execute("UPDATE season SET start_date = '2026-09-08' WHERE year = 2026 AND start_date IS NULL")
    op.execute("UPDATE season SET name = '2025 NFL' WHERE year = 2025 AND name IS NULL")
    op.execute("UPDATE season SET name = '2026 NFL' WHERE year = 2026 AND name IS NULL")

    # ------------------------------------------------------------------
    # game.season_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE game ADD COLUMN IF NOT EXISTS season_id INTEGER")
    op.execute("UPDATE game SET season_id = (SELECT id FROM season WHERE year = 2025) WHERE season_id IS NULL")
    op.execute("ALTER TABLE game ALTER COLUMN season_id SET NOT NULL")

    # Fresh/legacy DBs still have the old plain-unique constraint on
    # odds_event_id alone (from migration 432783cda776); production already
    # replaced it outside of Alembic tracking. Safe either way.
    op.execute("ALTER TABLE game DROP CONSTRAINT IF EXISTS uq_game_odds_event_id")

    op.execute(_add_constraint_if_missing(
        "uq_game_id_season", "game", "UNIQUE (id, season_id)"
    ))
    op.execute(_add_constraint_if_missing(
        "uq_game_season_odds_event_id", "game", "UNIQUE (season_id, odds_event_id)"
    ))
    op.execute(_add_constraint_if_missing(
        "game_season_id_fkey", "game", "FOREIGN KEY (season_id) REFERENCES season(id)"
    ))
    op.execute("CREATE INDEX IF NOT EXISTS idx_game_season_week ON game (season_id, week)")

    # ------------------------------------------------------------------
    # pick.season_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE pick ADD COLUMN IF NOT EXISTS season_id INTEGER")
    op.execute("""
        UPDATE pick SET season_id = (SELECT g.season_id FROM game g WHERE g.id = pick.game_id)
        WHERE season_id IS NULL
    """)
    op.execute("ALTER TABLE pick ALTER COLUMN season_id SET NOT NULL")

    op.execute(_add_constraint_if_missing(
        "pick_season_id_fkey", "pick", "FOREIGN KEY (season_id) REFERENCES season(id)"
    ))
    op.execute(_add_constraint_if_missing(
        "fk_pick_game_season", "pick",
        "FOREIGN KEY (game_id, season_id) REFERENCES game(id, season_id)"
    ))
    op.execute("CREATE INDEX IF NOT EXISTS idx_pick_season_game ON pick (season_id, game_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pick_season_user ON pick (season_id, user_id)")

    # ------------------------------------------------------------------
    # team_game_ats.season_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE team_game_ats ADD COLUMN IF NOT EXISTS season_id INTEGER")
    op.execute("""
        UPDATE team_game_ats SET season_id = (SELECT g.season_id FROM game g WHERE g.id = team_game_ats.game_id)
        WHERE season_id IS NULL
    """)
    op.execute("ALTER TABLE team_game_ats ALTER COLUMN season_id SET NOT NULL")

    op.execute("ALTER TABLE team_game_ats DROP CONSTRAINT IF EXISTS uq_team_game_once")

    op.execute(_add_constraint_if_missing(
        "team_game_ats_season_id_fkey", "team_game_ats", "FOREIGN KEY (season_id) REFERENCES season(id)"
    ))
    op.execute(_add_constraint_if_missing(
        "fk_tga_game_season", "team_game_ats",
        "FOREIGN KEY (game_id, season_id) REFERENCES game(id, season_id)"
    ))
    op.execute(_add_constraint_if_missing(
        "uq_season_game_team_once", "team_game_ats", "UNIQUE (season_id, game_id, team)"
    ))
    op.execute("CREATE INDEX IF NOT EXISTS idx_tga_season_team ON team_game_ats (season_id, team)")

    # ------------------------------------------------------------------
    # weekly_email_log.season_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE weekly_email_log ADD COLUMN IF NOT EXISTS season_id INTEGER")
    op.execute("UPDATE weekly_email_log SET season_id = (SELECT id FROM season WHERE year = 2025) WHERE season_id IS NULL")
    op.execute("ALTER TABLE weekly_email_log ALTER COLUMN season_id SET NOT NULL")

    op.execute("ALTER TABLE weekly_email_log DROP CONSTRAINT IF EXISTS uq_week_kind")

    op.execute(_add_constraint_if_missing(
        "weekly_email_log_season_id_fkey", "weekly_email_log", "FOREIGN KEY (season_id) REFERENCES season(id)"
    ))
    op.execute(_add_constraint_if_missing(
        "uq_season_week_kind", "weekly_email_log", "UNIQUE (season_id, week, kind)"
    ))
    op.execute("CREATE INDEX IF NOT EXISTS idx_email_log_season_week ON weekly_email_log (season_id, week)")

    # ------------------------------------------------------------------
    # user_season (per-season entry-fee tracking; mapped in models.py but
    # not yet wired into app logic)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_season (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            season_id INTEGER NOT NULL REFERENCES season(id) ON DELETE CASCADE,
            entry_paid BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_user_season UNIQUE (user_id, season_id)
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS user_season")

    with op.batch_alter_table('weekly_email_log', schema=None) as batch_op:
        batch_op.drop_constraint('weekly_email_log_season_id_fkey', type_='foreignkey')
        batch_op.drop_constraint('uq_season_week_kind', type_='unique')
        batch_op.drop_index('idx_email_log_season_week')
        batch_op.drop_column('season_id')
    op.execute("ALTER TABLE weekly_email_log ADD CONSTRAINT uq_week_kind UNIQUE (week, kind)")

    with op.batch_alter_table('team_game_ats', schema=None) as batch_op:
        batch_op.drop_constraint('fk_tga_game_season', type_='foreignkey')
        batch_op.drop_constraint('team_game_ats_season_id_fkey', type_='foreignkey')
        batch_op.drop_constraint('uq_season_game_team_once', type_='unique')
        batch_op.drop_index('idx_tga_season_team')
        batch_op.drop_column('season_id')
    op.execute("ALTER TABLE team_game_ats ADD CONSTRAINT uq_team_game_once UNIQUE (game_id, team)")

    with op.batch_alter_table('pick', schema=None) as batch_op:
        batch_op.drop_constraint('fk_pick_game_season', type_='foreignkey')
        batch_op.drop_constraint('pick_season_id_fkey', type_='foreignkey')
        batch_op.drop_index('idx_pick_season_game')
        batch_op.drop_index('idx_pick_season_user')
        batch_op.drop_column('season_id')

    with op.batch_alter_table('game', schema=None) as batch_op:
        batch_op.drop_constraint('game_season_id_fkey', type_='foreignkey')
        batch_op.drop_constraint('uq_game_id_season', type_='unique')
        batch_op.drop_constraint('uq_game_season_odds_event_id', type_='unique')
        batch_op.drop_index('idx_game_season_week')
        batch_op.drop_column('season_id')
    op.execute("ALTER TABLE game ADD CONSTRAINT uq_game_odds_event_id UNIQUE (odds_event_id)")

    op.execute("DROP TABLE IF EXISTS season")
