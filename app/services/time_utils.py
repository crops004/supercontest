# app/services/time_utils.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

def to_local(dt_utc: datetime, tzname: str | None) -> datetime:
    """
    Convert stored UTC datetime (naive or aware) to user timezone (aware).
    Assumes dt_utc is not None.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tzname) if tzname else ZoneInfo("UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    return dt_utc.astimezone(tz)

def round5(dt: datetime) -> datetime:
    """Round to nearest 5 minutes. Assumes dt is not None."""
    remainder = dt.minute % 5
    delta = -remainder if remainder < 3 else (5 - remainder)
    return (dt + timedelta(minutes=delta)).replace(second=0, microsecond=0)

def day_key(dt: datetime | None, tzname: str | None):
    """
    Returns (day_title, day_sort_key) using local tz and 5‑minute rounding.
    If dt is None -> ("TBD", None)
    """
    if dt is None:
        return ("TBD", None)
    local = round5(to_local(dt, tzname))
    return (local.strftime("%A"), local.date())

def time_key(dt: datetime | None, tzname: str | None):
    """
    Returns (time_title, time_sort_key) using local tz and 5‑minute rounding.
    If dt is None -> ("TBD", None)
    """
    if dt is None:
        return ("TBD", None)
    local = round5(to_local(dt, tzname))
    # Portable 12h hour without leading zero
    # Title like "3:25 PM MDT"
    title = local.strftime("%I:%M %p").lstrip("0") + " " + local.strftime("%Z")
    sort  = local.strftime("%H:%M")
    return (title, sort)

def fmt_local_with_tz(dt_utc: datetime | None, tzname: str | None) -> str:
    """Template-friendly '3:25 PM MDT' with 5‑minute rounding; '' if None."""
    if dt_utc is None:
        return ""
    local = round5(to_local(dt_utc, tzname))
    h = local.strftime('%I').lstrip('0') or '0'
    m = local.strftime('%M')
    ap = local.strftime('%p')
    abbr = local.strftime('%Z')
    return f"{h}:{m} {ap} {abbr}"
