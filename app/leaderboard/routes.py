from __future__ import annotations
from typing import Dict, List, Tuple
from collections import Counter

from flask import render_template, request
from flask_login import login_required, current_user
from sqlalchemy import func

from datetime import datetime, timezone
from app.extensions import db
from app.models import Pick, User, Game
from app.scoring import points_for_pick
from . import bp


# --- helpers ---

FALLBACK_FUTURE = datetime.max.replace(tzinfo=timezone.utc)

def get_current_week() -> int:
    """
    Current week = max week having at least one game with kickoff <= now().
    Falls back to earliest week if nothing has started yet.
    """
    kicked = db.session.query(func.max(Game.week)).filter(Game.kickoff_at <= func.now()).scalar()
    if kicked is not None:
        return int(kicked)
    first_week = db.session.query(func.min(Game.week)).scalar()
    return int(first_week or 0)


# --- routes ---
@bp.route("/", methods=["GET"], endpoint="leaderboard")
@login_required
def leaderboard():
    """
    Combined leaderboard:
      - Header shows displayed week with left/right arrows (clamped to [min_week, current_week])
      - Each row = user
        * 5 picks for displayed week: show user’s own picks pre‑kickoff; others show '—' until kickoff
        * This Week W-L-P (only graded picks)
        * Overall W-L-P through displayed week (only games that have started)
        * Points = 1/win, 0.5/push
    """
    cur_week = get_current_week()

    # Bounds for navigation
    min_week = db.session.query(func.min(Game.week)).scalar() or 0
    max_week = db.session.query(func.max(Game.week)).scalar() or 0

    mock_week = request.args.get("mock_week", type=int)
    if mock_week is not None:
        cur_week = max(min_week, min(mock_week, max_week))

    week_param = request.args.get("week", type=int)
    display_week = cur_week if week_param is None else max(min_week, min(week_param, cur_week))

    # All users (adjust ordering as you like)
    users = User.query.order_by(User.username.asc()).all()

    # First-name + last-initial formatting
    def split_name(u):
        raw = (getattr(u, "full_name", None) or u.username or "").strip()
        parts = raw.split()
        first = parts[0] if parts else u.username
        last_initial = parts[1][0].upper() if len(parts) > 1 and parts[1] else None
        return first, last_initial

    firsts, name_parts = [], {}
    for u in users:
        fn, li = split_name(u)
        firsts.append(fn)
        name_parts[u.id] = (fn, li)
    first_counts = Counter(firsts)

    # Games in the displayed week
    games_this_week: List[Game] = Game.query.filter_by(week=display_week).all()
    games_by_id_this_week = {g.id: g for g in games_this_week}

    # Picks for this week (for 5 cells + weekly W-L-P)
    picks_this_week = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.week == display_week)
        .all()
    )
    picks_by_user_this_week: Dict[int, List[Tuple[Pick, Game]]] = {}
    for p, g in picks_this_week:
        picks_by_user_this_week.setdefault(p.user_id, []).append((p, g))

    # Picks through this week (for season totals)
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
        # DEBUG: check ids
        print("DBG current_user.id =", current_user.id)
        print("DBG row user id =", u.id, "username=", u.username)
        weekly_picks = []  # list of {"label": str, "status": "win|loss|push|pending|hidden|empty|pre"}
        weekly_W = weekly_L = weekly_P = 0

        weekly_pairs = sorted(
            picks_by_user_this_week.get(u.id, []),
            key=lambda pg: (pg[1].kickoff_at or FALLBACK_FUTURE)
        )

        for p, g in weekly_pairs:
            started = g.has_started()

            if not started and u.id != current_user.id:
                # Not started AND not the viewing user → hide
                weekly_picks.append({"label": "—", "status": "hidden"})
                continue

            if started:
                # Started → grade if possible
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
                    status = "pending"  # started but not graded
            else:
                # Not started but this is the viewer’s own row → show as pre
                status = "pre"

            weekly_picks.append({"label": (p.chosen_team or ""), "status": status})

        # Normalize to exactly 5 cells
        while len(weekly_picks) < 5:
            weekly_picks.append({"label": "", "status": "empty"})
        weekly_picks = weekly_picks[:5]

        # Season totals through displayed week (only started games)
        season_W = season_L = season_P = 0
        season_points = 0.0
        for p, g in picks_by_user_to_date.get(u.id, []):
            if not g.has_started():
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
        display_name = f"{fn} {li}." if first_counts[fn] > 1 and li else fn

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

    # Sort rows for display
    rows.sort(key=lambda r: (-r["points"], -r["season_WLP"][0], r["season_WLP"][1], -r["season_WLP"][2], r["username"]))

    # Nav buttons
    show_left = display_week > min_week
    show_right = display_week < cur_week

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