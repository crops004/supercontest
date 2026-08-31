# app/services/week.py
from __future__ import annotations
from datetime import datetime, time
import zoneinfo
from app.services.odds_client import parse_iso_z
from app.services.season import get_current_season

DENVER = zoneinfo.ZoneInfo("America/Denver")

# --- config ---
def _week1_start_dt() -> datetime:
    """
    The current season's week-1 anchor (Denver-local midnight on
    Season.start_date), e.g. 2025-09-02 00:00 America/Denver. Sourced from
    the active Season row rather than a global env var, so each season can
    have its own start date.
    """
    season = get_current_season()
    if season.start_date is None:
        raise RuntimeError(f"Season {season.year} has no start_date set.")
    return datetime.combine(season.start_date, time.min, tzinfo=DENVER)

# --- public API ---
def week_for_kickoff(commence_time: str | datetime) -> int:
    """
    Given kickoff time (ISO string or datetime), return contest week.
      - Anything before Week 1 Tuesday 00:00 (Denver) is treated as Week 1
      - Otherwise compute week in 7-day buckets starting at Week 1 Tuesday
    """
    kickoff_at = (
        parse_iso_z(commence_time) if isinstance(commence_time, str) else commence_time
    ).astimezone(DENVER)

    start_dt = _week1_start_dt()

    # Clamp: before Week 1 start => Week 1
    if kickoff_at < start_dt:
        return 1

    days = (kickoff_at.date() - start_dt.date()).days
    # Week index is 0-based from start_dt; add 1 for human week number
    return max(1, (days // 7) + 1)

def current_week_number(now: datetime | None = None) -> int:
    """
    Return current contest week based on Denver local time.
      - Anything before Week 1 Tuesday 00:00 (Denver) is treated as Week 1
    """
    now_d = (now or datetime.now(DENVER)).astimezone(DENVER)
    start_dt = _week1_start_dt()

    # Clamp: before Week 1 start => Week 1
    if now_d < start_dt:
        return 1

    days = (now_d.date() - start_dt.date()).days
    return max(1, (days // 7) + 1)
