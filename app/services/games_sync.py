# app/services/games_sync.py
from typing import Any, Dict, Optional
from decimal import Decimal
from app.extensions import db
from app.models import Game
from app.services.odds_client import parse_iso_z
from app.services.week_utils import week_from_thursday

def _is_locked(game: Game) -> bool:
    return bool(getattr(game, "spread_is_locked", False))

def _extract_home_spread_from_event(event: Dict[str, Any]) -> Optional[float]:
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
    ext_id = event.get("id")
    kickoff_at = parse_iso_z(event["commence_time"])
    home = event["home_team"]
    away = event["away_team"]

    game = Game.query.filter_by(odds_event_id=ext_id).one_or_none()
    is_new = game is None
    if is_new:
        game = Game()
        game.odds_event_id = ext_id

    game.home_team = home
    game.away_team = away
    game.kickoff_at = kickoff_at
    if hasattr(game, "start_time"):
        game.start_time = kickoff_at

    # --- week handling ---
    if force_week is not None:
        game.week = force_week
    # else: don’t derive here — caller (refresh_lines_for_key) decides the week bucket

    if not _is_locked(game):
        home_spread = _extract_home_spread_from_event(event)
        if home_spread is not None:
            game.spread_home = Decimal(str(home_spread))
            if hasattr(game, "spread_away"):
                game.spread_away = Decimal(str(-home_spread))

    db.session.add(game)
    return game

def update_game_scores_from_score_event(game: Game, score_ev: Dict[str, Any]) -> None:
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
    if home_pts is not None and game.final_score_home != home_pts:
        game.final_score_home = home_pts
        changed = True
    if away_pts is not None and game.final_score_away != away_pts:
        game.final_score_away = away_pts
        changed = True

    if changed:
        db.session.add(game)
