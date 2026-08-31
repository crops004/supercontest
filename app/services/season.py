# app/services/season.py
from app.models import Season


def get_current_season() -> Season:
    """
    Returns the Season row flagged is_current=True.
    Raises if none is set — every environment must have exactly one current
    season (seeded by migration for existing data; set by the admin "start a
    new season" action going forward).
    """
    season = Season.query.filter_by(is_current=True).first()
    if season is None:
        raise RuntimeError("No current season is set (Season.is_current is True for no row).")
    return season


def current_season_id() -> int:
    return get_current_season().id
