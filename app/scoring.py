from app.models import Game, Pick

def game_result_against_spread(game: Game) -> str | None:
    """
    Returns: "home" | "away" | "push" | None  (None = not graded yet)

    Rules:
    - Apply the spread ONLY to the favorite (the side with a NEGATIVE spread).
      e.g. home -2.5 => adjusted_home = home_score - 2.5; adjusted_away = away_score
    - Pick'em (0/0): no adjustment.
    - If one side's spread is missing, derive it from the other.
    - All math in float to avoid Decimal mixing elsewhere.
    """
    # Need final scores
    if game.final_score_home is None or game.final_score_away is None:
        return None

    # Normalize scores to float
    home_score = float(game.final_score_home)
    away_score = float(game.final_score_away)

    # Normalize spreads (Decimal -> float), keep None as None
    sh = float(game.spread_home) if game.spread_home is not None else None
    sa = float(game.spread_away) if game.spread_away is not None else None

    # Derive the missing side if only one is present
    if sh is None and sa is not None:
        sh = -sa
    if sa is None and sh is not None:
        sa = -sh

    # If still both None -> pick'em (no adjustment)
    if sh is None and sa is None:
        adj_home, adj_away = home_score, away_score
    else:
        # Apply spread only to the favorite (negative number)
        adj_home = home_score + (sh if (sh is not None and sh < 0.0) else 0.0)
        adj_away = away_score + (sa if (sa is not None and sa < 0.0) else 0.0)

    # Compare with a tiny epsilon to avoid weird float ties (mostly unnecessary but safe)
    eps = 1e-9
    if adj_home > adj_away + eps:
        return "home"
    if adj_away > adj_home + eps:
        return "away"
    return "push"


def points_for_pick(pick: Pick, game: Game) -> float | None:
    """
    Returns 1.0 / 0.5 / 0.0, or None if game not graded yet.
    """
    res = game_result_against_spread(game)
    if res is None:
        return None
    if res == "push":
        return 0.5

    # Map pick to side
    if pick.chosen_team == game.home_team:
        side = "home"
    elif pick.chosen_team == game.away_team:
        side = "away"
    else:
        # If a pick references neither team, count it as a loss
        return 0.0

    return 1.0 if side == res else 0.0
