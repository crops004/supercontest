# app/services/lines_cycle.py
from decimal import Decimal
from flask import current_app
from app.extensions import db
from app.models import Game
from app.services.odds_client import fetch_odds, parse_iso_z
from app.services.games_sync import upsert_game_from_odds_event
from app.services.week_clock import current_week_number

def refresh_regular_lines() -> dict:
    """
    Pull DraftKings regular-season odds and upsert.
    Leaves locked games unchanged (games_sync already respects spread_is_locked).
    """
    events = fetch_odds(current_app.config["SPORT_KEYS"]["regular"])
    created = updated = 0
    for ev in events:
        existed = Game.query.filter_by(odds_event_id=ev["id"]).one_or_none() is not None
        upsert_game_from_odds_event(ev, force_week=None)  # week set later/elsewhere; ok
        created += 0 if existed else 1
        updated += 1 if existed else 0
    db.session.commit()
    return {"created": created, "updated": updated}

def lock_current_week() -> dict:
    """
    Lock spreads for the current week (and implicitly 'publish' them).
    After this, refreshes won't modify these games.
    """
    # We need a sample payload to determine week-1 Thursday if env var not set
    sample = fetch_odds(current_app.config["SPORT_KEYS"]["regular"])
    wk = current_week_number(sample)

    q = Game.query.filter(Game.week == wk, Game.spread_is_locked == False)  # noqa: E712
    updated = 0
    for g in q:
        g.spread_is_locked = True
        from datetime import datetime, timezone
        g.spread_locked_at = datetime.now(timezone.utc)
        updated += 1
    db.session.commit()
    return {"locked_week": wk, "games_locked": updated}
