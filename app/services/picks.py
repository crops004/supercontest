from sqlalchemy import func
from app.extensions import db
from app.models import Pick, Game
from app.services.week import current_week_number
from app.services.season import current_season_id

def remaining_picks_this_week(user_id: int, picks_per_week: int = 5) -> tuple[int, int]:
    """
    Returns (remaining, current_week). Remaining is clamped at >= 0.
    """
    if not user_id:
        return (0, current_week_number())

    wk = current_week_number()
    count = (
        db.session.query(func.count(Pick.id))
        .join(Game, Game.id == Pick.game_id)
        .filter(Pick.user_id == user_id, Game.week == wk, Game.season_id == current_season_id())
        .scalar()
        or 0
    )
    remaining = max(0, picks_per_week - int(count))
    return (remaining, wk)
