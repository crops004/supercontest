from flask import current_app
from sqlalchemy import and_
from typing import Any, Dict, Optional, Tuple, List
from decimal import Decimal
from app.extensions import db
from app.models import Game
from app.services.odds_client import parse_iso_z, fetch_odds, fetch_scores
from app.services.week import week_for_kickoff, current_week_number


DEFAULT_SPORT_KEYS = (
    "americanfootball_nfl",
    "americanfootball_nfl_preseason",
)

# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------
def _is_locked(game: Game) -> bool:
    return bool(getattr(game, "spread_is_locked", False))


def _extract_home_spread_from_event(event: Dict[str, Any]) -> Optional[float]:
    """
    Extract DraftKings home spread from an Odds API 'odds' event payload.
    """
    for bm in event.get("bookmakers", []):
        if bm.get("key") != "draftkings":
            continue
        for market in bm.get("markets", []):
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("name") == event.get("home_team") and "point" in outcome:
                    try:
                        return float(outcome["point"])
                    except (TypeError, ValueError):
                        return None
    return None


def upsert_game_from_odds_event(event: Dict[str, Any], *, force_week: Optional[int] = None) -> Game:
    """
    Create or update a Game from an Odds API event payload.
    - Honors locking for spread fields (other metadata can still update).
    - If force_week is provided, assigns Week explicitly (our Tuesday-anchored week).
    """
    ext_id = event.get("id")
    kickoff_at = parse_iso_z(event["commence_time"])
    home = event["home_team"]
    away = event["away_team"]

    game = Game.query.filter_by(odds_event_id=ext_id).one_or_none()
    is_new = game is None
    if is_new:
        game = Game()
        game.odds_event_id = ext_id

    # Always sync identity/kickoff; these are safe even when locked.
    game.home_team = home
    game.away_team = away
    game.kickoff_at = kickoff_at

    # --- week handling ---
    if force_week is not None:
        game.week = force_week
    # else: caller decides the week bucket

    # Update spreads only if unlocked
    if not _is_locked(game):
        home_spread = _extract_home_spread_from_event(event)
        if home_spread is not None:
            game.spread_home = Decimal(str(home_spread))
            if hasattr(game, "spread_away"):
                game.spread_away = Decimal(str(-home_spread))

    db.session.add(game)
    return game


def update_game_scores_from_score_event(game: Game, score_ev: Dict[str, Any]) -> bool:
    """
    Update final scores on the Game from an Odds API 'scores' payload.
    Returns True if the game was modified.
    """
    scores = score_ev.get("scores") or []
    name_to_pts: Dict[str, int] = {}
    for s in scores:
        nm = s.get("name")
        raw = s.get("score")
        if not nm or raw is None:
            continue
        try:
            name_to_pts[nm] = int(raw)
        except (TypeError, ValueError):
            continue

    home_pts = name_to_pts.get(game.home_team)
    away_pts = name_to_pts.get(game.away_team)

    changed = False
    # NOTE: using your column names final_score_home/final_score_away
    if home_pts is not None and getattr(game, "final_score_home", None) != home_pts:
        game.final_score_home = home_pts
        changed = True
    if away_pts is not None and getattr(game, "final_score_away", None) != away_pts:
        game.final_score_away = away_pts
        changed = True

    if changed:
        db.session.add(game)
    return changed


# -------------------------------------------------------------------
# Sync methods
# -------------------------------------------------------------------
def import_all_lines(*, sport_keys: Tuple[str, ...] = DEFAULT_SPORT_KEYS) -> Dict[str, int]:
    """
    Fetch odds for preseason + regular, compute week via Tuesday-anchored calendar,
    and upsert Games. Skips spread updates for locked games.
    Returns counters for reporting.
    """
    created = updated = skipped_locked = 0

    for key in sport_keys:
        payload = fetch_odds(key) or []
        for ev in payload:
            try:
                kickoff = parse_iso_z(ev.get("commence_time"))
                week = week_for_kickoff(kickoff)

                existing: Game | None = Game.query.filter_by(odds_event_id=ev.get("id")).one_or_none()
                if existing and _is_locked(existing):
                    skipped_locked += 1
                    continue

                before_exists = existing is not None
                upsert_game_from_odds_event(ev, force_week=week)

                if before_exists:
                    updated += 1
                else:
                    created += 1
            except Exception as ex:
                current_app.logger.exception(f"Failed to upsert odds event id={ev.get('id')}: {ex}")

    db.session.commit()
    return {"created": created, "updated": updated, "skipped_locked": skipped_locked}

def import_all_scores(*, sport_keys: Tuple[str, ...] = DEFAULT_SPORT_KEYS, days_from: int = 3) -> Dict[str, int]:
    """
    Pull scores for preseason + regular. Match on odds_event_id.
    Overwrite scores if different; no-op if unchanged.
    """
    updated_scores = missing_game = unchanged = 0

    for key in sport_keys:
        payload = fetch_scores(key, days_from=days_from) or []
        for ev in payload:
            try:
                event_id = ev.get("id")
                if not event_id:
                    continue

                game: Game | None = Game.query.filter_by(odds_event_id=event_id).one_or_none()
                if not game:
                    missing_game += 1
                    continue

                if update_game_scores_from_score_event(game, ev):
                    updated_scores += 1
                else:
                    unchanged += 1

            except Exception as ex:
                current_app.logger.exception(f"Failed to apply score for event id={ev.get('id')}: {ex}")

    db.session.commit()
    return {"updated_scores": updated_scores, "unchanged": unchanged, "missing_game": missing_game}


# -------------------------------------------------------------------
#  Lock weeks (this week and all previous)
# -------------------------------------------------------------------
def lock_weeks_through_current(*, include_preseason: bool = True) -> dict:
    """
    Based on Tuesday-anchored week, lock spreads for all games with week <= current week.
    """
    wk_now = current_week_number()
    # Only bail if we’re literally before preseason
    if wk_now < 0:
        return {"locked": 0, "week_now": wk_now}

    min_week = 0 if include_preseason else 1

    q = Game.query.filter(
        Game.week.between(min_week, wk_now),
        (Game.spread_is_locked.is_(False) | Game.spread_is_locked.is_(None))
    )

    count = 0
    from datetime import datetime, timezone
    for g in q:
        g.spread_is_locked = True
        if hasattr(g, "spread_locked_at"):
            g.spread_locked_at = datetime.now(timezone.utc)
        count += 1

    db.session.commit()
    return {"locked": count, "week_now": wk_now, "min_week": min_week}


# -------------------------------------------------------------------
#  Update spreads ONLY for UNLOCKED games
# -------------------------------------------------------------------
def refresh_spreads_unlocked(*, sport_keys: Tuple[str, ...] = DEFAULT_SPORT_KEYS) -> Dict[str, int]:
    """
    Re-fetch odds and update spreads ONLY for games that are not locked.
    Creates the game if missing; skips if locked.
    """
    created = updated = skipped_locked = 0

    for key in sport_keys:
        payload = fetch_odds(key) or []
        for ev in payload:
            try:
                event_id = ev.get("id")
                kickoff = parse_iso_z(ev.get("commence_time"))
                week = week_for_kickoff(kickoff)

                existing: Game | None = Game.query.filter_by(odds_event_id=event_id).one_or_none()
                if existing and _is_locked(existing):
                    skipped_locked += 1
                    continue

                before_exists = existing is not None
                upsert_game_from_odds_event(ev, force_week=week)

                if before_exists:
                    updated += 1
                else:
                    created += 1
            except Exception as ex:
                current_app.logger.exception(f"Failed to refresh spreads for {ev.get('id')}: {ex}")

    db.session.commit()
    return {"created": created, "updated": updated, "skipped_locked": skipped_locked}