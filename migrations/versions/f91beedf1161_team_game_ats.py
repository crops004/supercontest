"""team_game_ats

Revision ID: f91beedf1161
Revises: 2951a3086f64
Create Date: 2025-08-18 21:08:37.188987

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f91beedf1161'
down_revision = '2951a3086f64'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Create the enum IF NOT EXISTS (Postgres only)
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ats_result_enum') THEN
            CREATE TYPE ats_result_enum AS ENUM ('COVER', 'NO_COVER', 'PUSH');
        END IF;
    END$$;
    """)

    # 2) Reference the existing type (do NOT try to create it again)
    ats_enum = postgresql.ENUM('COVER', 'NO_COVER', 'PUSH',
                               name='ats_result_enum',
                               create_type=False)

    op.create_table(
        'team_game_ats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('game.id'), nullable=False, index=True),
        sa.Column('team', sa.String(), nullable=False),
        sa.Column('opponent', sa.String(), nullable=False),
        sa.Column('is_home', sa.Boolean(), nullable=False),
        sa.Column('closing_spread', sa.Numeric(5, 2), nullable=False),
        sa.Column('line_source', sa.String(length=64)),
        sa.Column('points_for', sa.Integer()),
        sa.Column('points_against', sa.Integer()),
        sa.Column('ats_result', ats_enum),        # <- uses existing enum
        sa.Column('cover_margin', sa.Numeric(5, 2)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('game_id', 'team', name='uq_team_game_once'),
    )


def downgrade():
    op.drop_table('team_game_ats')

    # Drop enum only if it's not used anywhere else
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ats_result_enum') THEN
            -- ensure no other table still references it before dropping
            DROP TYPE ats_result_enum;
        END IF;
    END$$;
    """)
