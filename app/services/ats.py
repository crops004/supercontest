from decimal import Decimal
from typing import Optional, Tuple
from app import db
from app.models import Game, TeamGameATS

def _get_or_create_row(game_id: int, team: Optional[str]) -> TeamGameATS:
    """Fetch the TeamGameATS row for (game_id, team), or create a new one."""
    team_str = team or ""  # avoid None for strict type checkers

    row = TeamGameATS.query.filter_by(game_id=game_id, team=team_str).first()
    if row is None:
        # Create empty and set attributes explicitly (pylance-friendly)
        row = TeamGameATS()  # type: ignore[call-arg]
        row.game_id = game_id
        row.team = team_str
        row.opponent = ""
        row.is_home = False
        row.closing_spread = Decimal("0")
        db.session.add(row)
    return row

def snapshot_closing_lines_for_game(game: Game, line_source: Optional[str] = None) -> None:
    """
    Call this when you set spread_is_locked=True for a game.
    Snapshots closing spreads for both teams.
    """
    # HOME
    home = _get_or_create_row(game.id, game.home_team)
    home.opponent = game.away_team or ""
    home.is_home = True
    home.closing_spread = Decimal(str(game.spread_home or 0))
    home.line_source = line_source

    # AWAY
    away = _get_or_create_row(game.id, game.away_team)
    away.opponent = game.home_team or ""
    away.is_home = False
    away.closing_spread = Decimal(str(game.spread_away or 0))
    away.line_source = line_source

def _compute_ats(points_for: int, points_against: int, closing_spread: Decimal) -> Tuple[str, Decimal]:
    """
    cover_margin = (points_for + closing_spread) - points_against
    Returns ('COVER' | 'PUSH' | 'NO_COVER', cover_margin)
    """
    margin = Decimal(points_for) + Decimal(closing_spread) - Decimal(points_against)
    if margin > 0:
        return 'COVER', margin
    elif margin == 0:
        return 'PUSH', margin
    else:
        return 'NO_COVER', margin

def finalize_ats_for_game(game: Game) -> None:
    """
    Call when final scores are set. Fills points_for/against, ats_result, cover_margin.
    """
    if game.final_score_home is None or game.final_score_away is None:
        return

    # HOME
    home = _get_or_create_row(game.id, game.home_team)
    home.opponent = game.away_team or ""
    home.is_home = True
    home.points_for = int(game.final_score_home)
    home.points_against = int(game.final_score_away)
    if home.closing_spread is None:
        home.closing_spread = Decimal(str(game.spread_home or 0))
    home.ats_result, home.cover_margin = _compute_ats(home.points_for, home.points_against, home.closing_spread)

    # AWAY
    away = _get_or_create_row(game.id, game.away_team)
    away.opponent = game.home_team or ""
    away.is_home = False
    away.points_for = int(game.final_score_away)
    away.points_against = int(game.final_score_home)
    if away.closing_spread is None:
        away.closing_spread = Decimal(str(game.spread_away or 0))
    away.ats_result, away.cover_margin = _compute_ats(away.points_for, away.points_against, away.closing_spread)
