# app/services/season.py
from app.models import Season


def get_current_season() -> Season:
    """
    Returns the Season row flagged is_active=True.
    Raises if none is set — every environment must have exactly one active
    season (seeded by migration for existing data; set by the admin "start a
    new season" action going forward). The database itself enforces that at
    most one row can be active (a partial unique index on is_active).
    """
    season = Season.query.filter_by(is_active=True).first()
    if season is None:
        raise RuntimeError("No active season is set (Season.is_active is True for no row).")
    return season


def current_season_id() -> int:
    return get_current_season().id
