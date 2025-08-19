# app/services/week_utils.py
from datetime import datetime, timezone, timedelta

def parse_iso_z(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)

def resolve_week1_thursday_utc(payload: list[dict], fallback_now: datetime | None = None) -> datetime:
    # If you prefer an env override, read it in config and pass it here.
    earliest = None
    for ev in payload or []:
        ct = ev.get("commence_time")
        if not ct:
            continue
        dt = parse_iso_z(ct)
        if earliest is None or dt < earliest:
            earliest = dt

    base = earliest or (fallback_now or datetime.now(timezone.utc))
    # snap back to the Thursday of that week (Thu=3)
    days_back = (base.weekday() - 3) % 7
    thu = base - timedelta(days=days_back,
                           hours=base.hour, minutes=base.minute,
                           seconds=base.second, microseconds=base.microsecond)
    return thu.replace(tzinfo=timezone.utc)

def week_from_thursday(kickoff_at: datetime, week1_thu_utc: datetime) -> int:
    k = kickoff_at.astimezone(timezone.utc)
    if k < week1_thu_utc:
        return 0  # preseason
    delta_days = (k - week1_thu_utc).days
    return (delta_days // 7) + 1
