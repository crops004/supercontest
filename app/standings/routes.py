from __future__ import annotations
from typing import Dict, List, Tuple
from collections import Counter

from flask import render_template, request
from flask_login import login_required, current_user
from sqlalchemy import func

from datetime import datetime, timezone
from app.extensions import db
from app.models import Pick, User, Game, TeamGameATS
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
@bp.route("/", methods=["GET"], endpoint="standings")
@login_required
def standings():
    """
    Combined standings:
      - Header shows displayed week with left/right arrows (clamped to [min_week, current_week])
      - Each row = user
        * 5 picks for displayed week: show user’s own picks pre-kickoff; others show '—' until kickoff
        * This Week W-L-P (only graded picks)
        * Overall W-L-P through displayed week (only games that have started)
        * Points = 1/win, 0.5/push
    """
    # --- small helpers (scoped here for clarity) ---
    def is_final(g: Game) -> bool:
        completed = getattr(g, "completed", None)
        if completed is not None:
            return bool(completed)
        return (g.final_score_home is not None and g.final_score_away is not None)

    def status_from_ats_or_score(p: Pick, g: Game, ats_by_game: dict | None) -> str:
        """
        Return 'win' | 'loss' | 'push' | 'pending'.
        Prefer ATS; if missing, only grade by score when FINAL; else pending.
        """
        if ats_by_game is not None:
            ats = ats_by_game.get((g.id, p.chosen_team))
            if ats == "COVER":    return "win"
            if ats == "NO_COVER": return "loss"
            if ats == "PUSH":     return "push"
        if not is_final(g):
            return "pending"
        pts = points_for_pick(p, g)
        if pts == 1.0:  return "win"
        if pts == 0.5:  return "push"
        if pts == 0.0:  return "loss"
        return "pending"

    # --- determine display week / bounds ---
    cur_week = get_current_week()
    min_week = db.session.query(func.min(Game.week)).scalar() or 0
    max_week = db.session.query(func.max(Game.week)).scalar() or 0

    mock_week = request.args.get("mock_week", type=int)
    if mock_week is not None:
        cur_week = max(min_week, min(mock_week, max_week))

    week_param = request.args.get("week", type=int)
    display_week = cur_week if week_param is None else max(min_week, min(week_param, cur_week))

    # --- users & name formatting (First or First L.) ---
    users = User.query.order_by(User.username.asc()).all()

    def split_name(u: User) -> tuple[str, str | None]:
        """
        Returns (first, last_initial or None).
        Falls back to username if first_name is missing.
        """
        first = (getattr(u, "first_name", None) or "").strip()
        last  = (getattr(u, "last_name",  None) or "").strip()

        if not first:
            first = (u.username or "").strip()

        last_initial = last[0].upper() if last else None
        return first, last_initial

    from collections import Counter

    first_keys: list[str] = []
    name_parts: dict[int, tuple[str, str | None]] = {}

    for u in users:
        fn, li = split_name(u)
        name_parts[u.id] = (fn, li)
        first_keys.append(fn.casefold())

    first_counts = Counter(first_keys)

    def display_name_for(u: User) -> str:
        fn, li = name_parts[u.id]
        needs_initial = first_counts[fn.casefold()] > 1
        if needs_initial and li:
            return f"{fn} {li}."
        return fn

    # --- data for this week ---
    games_this_week: List[Game] = Game.query.filter_by(week=display_week).all()
    game_ids_week = [g.id for g in games_this_week]
    ats_rows_week = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids_week)).all() if game_ids_week else []
    ats_by_game_week = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows_week}

    picks_this_week = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.week == display_week)
        .all()
    )
    picks_by_user_this_week: Dict[int, List[Tuple[Pick, Game]]] = {}
    for p, g in picks_this_week:
        picks_by_user_this_week.setdefault(p.user_id, []).append((p, g))

    # --- data through this week (season totals) ---
    picks_through_week = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.week <= display_week)
        .all()
    )
    picks_by_user_to_date: Dict[int, List[Tuple[Pick, Game]]] = {}
    for p, g in picks_through_week:
        picks_by_user_to_date.setdefault(p.user_id, []).append((p, g))

    game_ids_to_date = [g.id for _, g in picks_through_week]
    ats_rows_to_date = (
        TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids_to_date)).all()
        if game_ids_to_date else []
    )
    ats_by_game_to_date = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows_to_date}

    # --- build rows ---
    rows: List[Dict] = []
    for u in users:
        weekly_picks: List[Dict] = []  # {"label": str, "status": "win|loss|push|pending|hidden|pre|empty"}
        weekly_W = weekly_L = weekly_P = 0

        weekly_pairs = sorted(
            picks_by_user_this_week.get(u.id, []),
            key=lambda pg: (pg[1].kickoff_at or FALLBACK_FUTURE),
        )

        for p, g in weekly_pairs:
            started = g.has_started()
            final = is_final(g)

            # Hide others' picks before kickoff
            if not started and u.id != current_user.id:
                weekly_picks.append({"label": "—", "status": "hidden"})
                continue

            if final:
                status = status_from_ats_or_score(p, g, ats_by_game_week)
            else:
                # Not final: show 'pending' after kickoff; show 'pre' before kickoff for own row
                status = "pending" if started else ("pre" if u.id == current_user.id else "hidden")

            if status == "win":
                weekly_W += 1
            elif status == "loss":
                weekly_L += 1
            elif status == "push":
                weekly_P += 1

            weekly_picks.append({"label": (p.chosen_team or ""), "status": status})

        # Normalize to exactly 5 cells
        while len(weekly_picks) < 5:
            weekly_picks.append({"label": "", "status": "empty"})
        weekly_picks = weekly_picks[:5]

        # Season totals through displayed week (final games only; prefer ATS)
        season_W = season_L = season_P = 0
        season_points = 0.0
        for p, g in picks_by_user_to_date.get(u.id, []):
            if not is_final(g):
                continue
            ats = ats_by_game_to_date.get((g.id, p.chosen_team))
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
            season_points += float(pts)
            if pts == 1.0:
                season_W += 1
            elif pts == 0.5:
                season_P += 1
            elif pts == 0.0:
                season_L += 1

        display_name = display_name_for(u)

        rows.append({
            "user_id": u.id,
            "username": u.username,
            "display_name": display_name,
            "weekly_picks": weekly_picks,
            "week_WLP": (weekly_W, weekly_L, weekly_P),
            "season_WLP": (season_W, season_L, season_P),  # keep your original order
            "points": season_points,
        })

    # Sort rows for display (unchanged)
    rows.sort(key=lambda r: (-r["points"], -r["season_WLP"][0], r["season_WLP"][1], -r["season_WLP"][2], r["username"]))

    # Nav buttons
    show_left = display_week > min_week
    show_right = display_week < cur_week

    return render_template(
        "standings.html",
        rows=rows,
        display_week=display_week,
        current_week=cur_week,
        show_left=show_left,
        show_right=show_right,
        mock_week=mock_week,
        min_week=min_week,
        max_week=max_week,
    )