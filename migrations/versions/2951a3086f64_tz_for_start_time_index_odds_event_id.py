"""TZ for start_time; index odds_event_id

Revision ID: 2951a3086f64
Revises: 691790037436
Create Date: 2025-08-15 15:11:16.618915

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2951a3086f64'
down_revision = '691790037436'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    # --- Make start_time timezone-aware (assume existing naive values are UTC) ---
    if dialect == "postgresql":
        op.alter_column(
            "game",
            "start_time",
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
            postgresql_using="start_time AT TIME ZONE 'UTC'"
        )
    else:
        # SQLite (or other) fallback: use batch alter (SQLite ignores timezone flag, but keeps data)
        with op.batch_alter_table("game") as batch_op:
            batch_op.alter_column(
                "start_time",
                type_=sa.DateTime(timezone=True),
                existing_nullable=False,
            )


def downgrade():

    bind = op.get_bind()
    dialect = bind.dialect.name

    # Revert start_time to naive (timezone=False)
    if dialect == "postgresql":
        op.alter_column(
            "game",
            "start_time",
            type_=sa.DateTime(timezone=False),
            existing_nullable=False,
            postgresql_using="(start_time AT TIME ZONE 'UTC')"  # convert back to naive in UTC
        )
    else:
        with op.batch_alter_table("game") as batch_op:
            batch_op.alter_column(
                "start_time",
                type_=sa.DateTime(timezone=False),
                existing_nullable=False,
            )
