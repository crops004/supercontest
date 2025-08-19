# app/services/week_clock.py
from datetime import datetime, timezone, timedelta
import zoneinfo
from app.services.week_utils import resolve_week1_thursday_utc, week_from_thursday
from app.services.odds_client import parse_iso_z  # already returns aware UTC
import os

DENVER = zoneinfo.ZoneInfo("America/Denver")

def now_denver() -> datetime:
    return datetime.now(DENVER)

def current_week_number(events_sample: list[dict]) -> int:
    """
    Determine current NFL week number right now.
    Uses NFL_WEEK1_THURSDAY_UTC if set; else infers from sample odds payload.
    """
    env_val = os.getenv("NFL_WEEK1_THURSDAY_UTC")
    if env_val:
        wk1 = parse_iso_z(env_val)
    else:
        wk1 = resolve_week1_thursday_utc(events_sample)
    return week_from_thursday(now_denver().astimezone(timezone.utc), wk1)
