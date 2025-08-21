# app/services/lines_cycle.py
from decimal import Decimal
from flask import current_app
from app.extensions import db
from app.models import Game
from app.services.odds_client import fetch_odds, parse_iso_z
from app.services.games_sync import upsert_game_from_odds_event
from app.services.week_clock import current_week_number
from app.services.week_utils import week_from_thursday, resolve_week1_thursday_utc

# def refresh_regular_lines() -> dict:
#     """
#     Pull DraftKings regular-season odds and upsert.
#     Leaves locked games unchanged (games_sync already respects spread_is_locked).
#     """
#     events = fetch_odds(current_app.config["SPORT_KEYS"]["regular"])
#     created = updated = 0
#     for ev in events:
#         existed = Game.query.filter_by(odds_event_id=ev["id"]).one_or_none() is not None
#         upsert_game_from_odds_event(ev, force_week=None)  # week set later/elsewhere; ok
#         created += 0 if existed else 1
#         updated += 1 if existed else 0
#     db.session.commit()
#     return {"created": created, "updated": updated}

def refresh_lines_for_key(sport_key: str, *, force_week: int | None) -> dict:
    """
    Fetch odds for the given key and upsert games.
    If force_week is None, compute week from payload.
    Returns: {"week": int, "created": int, "updated": int}
    """
    events = fetch_odds(sport_key) or []

    # Decide the target week bucket
    if force_week is not None:
        resolved_week = force_week
    else:
        wk1 = resolve_week1_thursday_utc(events)
        if events:
            first_kick = parse_iso_z(events[0]["commence_time"])
            resolved_week = week_from_thursday(first_kick, wk1)
        else:
            resolved_week = 0  # no events -> harmless fallback

    created = 0
    updated = 0
    for ev in events:
        existed = Game.query.filter_by(odds_event_id=ev.get("id")).one_or_none() is not None
        # Always pass the resolved week to avoid silent week=0
        upsert_game_from_odds_event(ev, force_week=resolved_week)
        if existed:
            updated += 1
        else:
            created += 1

    db.session.commit()
    return {"week": resolved_week, "created": created, "updated": updated}

def lock_current_week(week: int | None = None, *, season_type: str = "regular") -> dict:
    keys = current_app.config["SPORT_KEYS"]
    sport_key = keys.get(season_type, keys["regular"])

    if week is None:
        sample = fetch_odds(sport_key) or []
        week = current_week_number(sample)

    q = Game.query.filter(Game.week == week, Game.spread_is_locked == False)  # noqa: E712
    updated = 0
    from datetime import datetime, timezone
    for g in q:
        g.spread_is_locked = True
        g.spread_locked_at = datetime.now(timezone.utc)
        updated += 1

    db.session.commit()
    return {"locked_week": week, "games_locked": updated}
