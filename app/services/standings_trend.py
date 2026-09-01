# app/services/standings_trend.py
from collections import Counter
from typing import Dict, List, Optional

from app.extensions import db
from app.models import Pick, Game, TeamGameATS, User
from app.scoring import points_for_pick
from app.services.roster import roster_user_ids

# NFL regular season is weeks 1-18. Week 19 only ever gets used if a tiebreaker
# game is needed - it should never silently count toward normal standings/history.
REGULAR_SEASON_WEEKS = 18


def _display_names(users: List[User]) -> Dict[int, str]:
    """Same 'First' / 'First L.' disambiguation used elsewhere on the standings page."""
    def split_name(u: User):
        first = (getattr(u, "first_name", None) or "").strip()
        last = (getattr(u, "last_name", None) or "").strip()
        if not first:
            first = (u.username or "").strip()
        return first, (last[0].upper() if last else None)

    parts = {u.id: split_name(u) for u in users}
    first_counts = Counter(fn.casefold() for fn, _ in parts.values())

    names = {}
    for u in users:
        fn, li = parts[u.id]
        if first_counts[fn.casefold()] > 1 and li:
            names[u.id] = f"{fn} {li}."
        else:
            names[u.id] = fn
    return names


def _roster_users(season_id: int) -> List[User]:
    """Users playing this season, ordered by username (falls back to everyone
    if this season predates roster tracking)."""
    ids = roster_user_ids(season_id)
    q = User.query
    if ids is not None:
        q = q.filter(User.id.in_(ids))
    return q.order_by(User.username.asc()).all()


def get_cumulative_points_trend(season_id: int, through_week: int) -> dict:
    """
    Returns {"weeks": [1..through_week], "series": [{"user_id", "display_name", "cumulative": [...]}, ...]}
    `cumulative[i]` is each user's total points through weeks[i].
    """
    users = _roster_users(season_id)
    display_names = _display_names(users)

    pairs = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.season_id == season_id, Game.week <= through_week)
        .all()
    )

    game_ids = [g.id for _, g in pairs]
    ats_rows = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all() if game_ids else []
    ats_by_game = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows}

    def is_final(g: Game) -> bool:
        comp = getattr(g, "completed", None)
        if comp is not None:
            return bool(comp)
        return g.final_score_home is not None and g.final_score_away is not None

    points_by_user_week: Dict[int, Dict[int, float]] = {}
    for p, g in pairs:
        if not is_final(g):
            continue
        ats = ats_by_game.get((g.id, p.chosen_team))
        if ats == "COVER":
            pts = 1.0
        elif ats == "PUSH":
            pts = 0.5
        elif ats == "NO_COVER":
            pts = 0.0
        else:
            pts = points_for_pick(p, g)
        if pts is None:
            continue
        points_by_user_week.setdefault(p.user_id, {})
        points_by_user_week[p.user_id][g.week] = points_by_user_week[p.user_id].get(g.week, 0.0) + pts

    weeks = list(range(1, through_week + 1))
    series = []
    for u in users:
        weekly = points_by_user_week.get(u.id, {})
        cumulative = []
        running = 0.0
        for wk in weeks:
            running += weekly.get(wk, 0.0)
            cumulative.append(running)
        series.append({
            "user_id": u.id,
            "display_name": display_names[u.id],
            "cumulative": cumulative,
        })

    return {"weeks": weeks, "series": series}


def get_final_standings(season_id: int, max_week: Optional[int] = None) -> List[dict]:
    """
    Final leaderboard for a (typically completed) season:
    [{"user_id", "display_name", "w", "l", "p", "points", "rank"}, ...] sorted
    the same way as the live standings page (points, then W, then L, then P,
    then name). Pass max_week to exclude weeks beyond it (e.g. a week-19
    tiebreaker game) from the totals.
    """
    users = _roster_users(season_id)
    display_names = _display_names(users)

    query = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.season_id == season_id)
    )
    if max_week is not None:
        query = query.filter(Game.week <= max_week)
    pairs = query.all()
    game_ids = [g.id for _, g in pairs]
    ats_rows = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all() if game_ids else []
    ats_by_game = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows}

    def is_final(g: Game) -> bool:
        comp = getattr(g, "completed", None)
        if comp is not None:
            return bool(comp)
        return g.final_score_home is not None and g.final_score_away is not None

    totals: Dict[int, Dict[str, float]] = {}
    for p, g in pairs:
        if not is_final(g):
            continue
        ats = ats_by_game.get((g.id, p.chosen_team))
        if ats == "COVER":
            pts = 1.0
        elif ats == "PUSH":
            pts = 0.5
        elif ats == "NO_COVER":
            pts = 0.0
        else:
            pts = points_for_pick(p, g)
        if pts is None:
            continue
        t = totals.setdefault(p.user_id, {"w": 0, "l": 0, "p": 0, "points": 0.0})
        t["points"] += float(pts)
        if pts == 1.0:
            t["w"] += 1
        elif pts == 0.5:
            t["p"] += 1
        elif pts == 0.0:
            t["l"] += 1

    rows = []
    for u in users:
        t = totals.get(u.id, {"w": 0, "l": 0, "p": 0, "points": 0.0})
        rows.append({
            "user_id": u.id,
            "display_name": display_names[u.id],
            "w": int(t["w"]), "l": int(t["l"]), "p": int(t["p"]),
            "points": t["points"],
        })

    rows.sort(key=lambda r: (-r["points"], -r["w"], r["l"], -r["p"], r["display_name"].lower()))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows
