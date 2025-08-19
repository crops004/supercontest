from __future__ import annotations
from typing import Dict, List, Tuple
from datetime import datetime, timezone
from collections import Counter

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models import Pick, User, Game
from . import bp

# --- helpers ---

def utcnow() -> datetime:
    # timezone-aware "now" in UTC
    return datetime.now(timezone.utc)

def to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    # If dt has no tzinfo, assume it's already UTC from your DB and make it aware
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    # Otherwise convert to UTC
    return dt.astimezone(timezone.utc)

def get_current_week() -> int:
    """
    Define current week as the max week that has at least one game with kickoff_at <= now.
    Falls back to 1 if no games exist.
    """
    now = datetime.utcnow()
    # Highest week that has at least one game already kicked
    kicked = db.session.query(func.max(Game.week)).filter(Game.kickoff_at <= now).scalar()
    if kicked is not None:
        return int(kicked)
    # Nothing has kicked yet → use the earliest week on the schedule (likely 0)
    first_week = db.session.query(func.min(Game.week)).scalar()
    return int(first_week or 0)

def game_has_kicked_off(game: Game) -> bool:
    return bool(game and game.kickoff_at and game.kickoff_at <= utcnow())

# If your points_for_pick is elsewhere, import it; otherwise adapt here.
from app.scoring import points_for_pick  # keep using your logic that returns 1.0 / 0.5 / 0.0 / None

@bp.route("/", methods=["GET"], endpoint="leaderboard")
@login_required
def leaderboard():
    """
    Combined leaderboard:
    - Header shows displayed week with left/right arrows (clamped to [1, current_week])
    - Each row = user
      * 5 picks for displayed week (team nickname); hidden as '—' until kickoff
      * This Week W-L-P (only graded picks)
      * Overall W-L-P through displayed week (only graded picks that have kicked off)
      * Points (1 per win, 0.5 per push; derived from your points_for_pick)
    """
    cur_week = get_current_week()

    mock_week = request.args.get("mock_week", type=int)
    if mock_week is not None:
        # Clamp mock to available schedule range
        min_week = db.session.query(func.min(Game.week)).scalar() or 0
        max_week = db.session.query(func.max(Game.week)).scalar() or 0
        cur_week = max(min_week, min(mock_week, max_week))
    else:
        min_week = db.session.query(func.min(Game.week)).scalar() or 0
        max_week = db.session.query(func.max(Game.week)).scalar() or 0

    week_param = request.args.get("week", type=int)
    display_week = cur_week if week_param is None else max(min_week, min(week_param, cur_week))

    # Load all users participating (adjust ordering as you like)
    users = User.query.order_by(User.username.asc()).all()

    # Build first-name frequencies
    def split_name(u):
        # Prefer full_name, else try username as "First Last" or just "First"
        raw = (getattr(u, "full_name", None) or u.username or "").strip()
        parts = raw.split()
        first = parts[0] if parts else u.username
        last_initial = parts[1][0].upper() if len(parts) > 1 and parts[1] else None
        return first, last_initial

    firsts = []
    name_parts = {}
    for u in users:
        fn, li = split_name(u)
        firsts.append(fn)
        name_parts[u.id] = (fn, li)

    first_counts = Counter(firsts)

    # Games for the displayed week (to know kickoff & to hide/show picks)
    games_this_week: List[Game] = Game.query.filter_by(week=display_week).all()
    games_by_id_this_week = {g.id: g for g in games_this_week}

    # --- Picks for this week (to render the 5 picks + weekly W-L-P) ---
    picks_this_week = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.week == display_week)
        .all()
    )

    picks_by_user_this_week: Dict[int, List[Tuple[Pick, Game]]] = {}
    for p, g in picks_this_week:
        picks_by_user_this_week.setdefault(p.user_id, []).append((p, g))

    # --- Picks through this week (for season/overall W-L-P and points) ---
    picks_through_week = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.week <= display_week)
        .all()
    )

    picks_by_user_to_date: Dict[int, List[Tuple[Pick, Game]]] = {}
    for p, g in picks_through_week:
        picks_by_user_to_date.setdefault(p.user_id, []).append((p, g))

    # Build rows
    rows: List[Dict] = []
    for u in users:
        # --- Weekly picks list (exactly 5 cells) & weekly W-L-P ---
        weekly_picks = []  # list of {"label": str, "status": "win|loss|push|pending|hidden|empty"}
        weekly_W = weekly_L = weekly_P = 0

        weekly_pairs = sorted(
            picks_by_user_this_week.get(u.id, []),
            key=lambda pg: (pg[1].kickoff_at or datetime.utcnow())
        )

        for p, g in weekly_pairs:
            if not game_has_kicked_off(g):
                weekly_picks.append({"label": "—", "status": "hidden"})
                continue

            pts = points_for_pick(p, g)
            if pts == 1.0:
                weekly_W += 1
                status = "win"
            elif pts == 0.5:
                weekly_P += 1
                status = "push"
            elif pts == 0.0:
                weekly_L += 1
                status = "loss"
            else:
                # kicked off but not graded yet
                status = "pending"

            weekly_picks.append({"label": (p.chosen_team or ""), "status": status})

        # Normalize to exactly 5 cells
        while len(weekly_picks) < 5:
            weekly_picks.append({"label": "", "status": "empty"})
        weekly_picks = weekly_picks[:5]

        # --- Season/overall W-L-P and points through displayed week ---
        season_W = season_L = season_P = 0
        season_points = 0.0

        for p, g in picks_by_user_to_date.get(u.id, []):
            # Only count if game has kicked (avoids leaking future picks)
            if not game_has_kicked_off(g):
                continue
            pts = points_for_pick(p, g)
            if pts is None:
                continue
            season_points += float(pts)
            if pts == 1.0:
                season_W += 1
            elif pts == 0.5:
                season_P += 1
            elif pts == 0.0:
                season_L += 1

        fn, li = name_parts[u.id]
        if first_counts[fn] > 1 and li:
            display_name = f"{fn} {li}."
        else:
            display_name = fn

        rows.append({
            "user_id": u.id,
            "username": u.username,
            "full_name": getattr(u, "full_name", None),
            "display_name": display_name,
            "weekly_picks": weekly_picks,
            "week_WLP": (weekly_W, weekly_L, weekly_P),
            "season_WLP": (season_W, season_L, season_P),
            "points": season_points,
        })

    # Sort rows for display (points desc, wins desc, losses asc, pushes desc, username)
    rows.sort(key=lambda r: (-r["points"], -r["season_WLP"][0], r["season_WLP"][1], -r["season_WLP"][2], r["username"]))

    # Nav buttons
    show_left = display_week > 0
    show_right = display_week < cur_week

    print("DBG week:", {"display_week": display_week, "current_week": cur_week,
                   "show_left": show_left, "show_right": show_right})
    

    return render_template(
        "leaderboard_combined.html",
        rows=rows,
        display_week=display_week,
        current_week=cur_week,
        show_left=show_left,
        show_right=show_right,
        mock_week=mock_week,
        min_week=min_week,
        max_week=max_week,
    )
