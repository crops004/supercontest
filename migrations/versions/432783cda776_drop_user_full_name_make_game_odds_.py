"""Drop user.full_name; make game.odds_event_id unique

Revision ID: 432783cda776
Revises: 37aa17390959
Create Date: 2025-08-26 15:11:14.025112

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '432783cda776'
down_revision = '37aa17390959'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ---- GAME ----
    existing_indexes = {ix["name"] for ix in insp.get_indexes("game")}
    with op.batch_alter_table("game") as batch_op:
        # drop only if present
        if "ux_game_match_kickoff" in existing_indexes:
            batch_op.drop_index("ux_game_match_kickoff")
        if "ux_game_odds_event_id" in existing_indexes:
            batch_op.drop_index("ux_game_odds_event_id")

        # enforce uniqueness on odds_event_id (prefer a constraint name `uq_...`)
        # NOTE: a unique constraint will usually create an index under the hood
        batch_op.create_unique_constraint("uq_game_odds_event_id", ["odds_event_id"])

    # ---- USER ----
    user_cols = {c["name"] for c in insp.get_columns("user")}
    if "full_name" in user_cols:
        with op.batch_alter_table("user") as batch_op:
            batch_op.drop_column("full_name")


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ---- USER ----
    user_cols = {c["name"] for c in insp.get_columns("user")}
    if "full_name" not in user_cols:
        with op.batch_alter_table("user") as batch_op:
            batch_op.add_column(sa.Column("full_name", sa.String(length=120), nullable=True))

    # ---- GAME ----
    game_indexes = {ix["name"] for ix in insp.get_indexes("game")}
    with op.batch_alter_table("game") as batch_op:
        # drop constraint if it exists
        # Alembic doesn't have if_exists here, so check via inspector:
        if "uq_game_odds_event_id" in game_indexes:
            # On some DBs the unique constraint appears as an index name.
            # If your dialect tracks constraints separately, use drop_constraint:
            pass
        try:
            batch_op.drop_constraint("uq_game_odds_event_id", type_="unique")
        except Exception:
            # Fallback if the dialect exposes it as an index
            if "uq_game_odds_event_id" in game_indexes:
                batch_op.drop_index("uq_game_odds_event_id")

        # Recreate your old indexes if you really want them back:
        if "ux_game_odds_event_id" not in game_indexes:
            batch_op.create_index("ux_game_odds_event_id", ["odds_event_id"], unique=True)
        if "ux_game_match_kickoff" not in game_indexes:
            batch_op.create_index("ux_game_match_kickoff", ["home_team", "away_team", "kickoff_at"], unique=True)
