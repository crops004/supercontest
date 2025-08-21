# app/admin_routes.py
from flask import Blueprint, request, jsonify, current_app, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Game, Pick, User, TeamGameATS
from app.scoring import points_for_pick
from app.services.odds_client import fetch_odds, fetch_scores
from app.services.games_sync import upsert_game_from_odds_event, update_game_scores_from_score_event
from app.services.lines_cycle import lock_current_week, refresh_lines_for_key
from .routes import _day_key, _time_key
from collections import defaultdict
from typing import List, Dict
from datetime import datetime, timezone
from collections import OrderedDict
from app.services.ats import snapshot_closing_lines_for_game, finalize_ats_for_game

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------- small helpers (DRY) ----------

# Defaults if not overridden in app.config["SPORT_KEYS"]
DEFAULT_SPORT_KEYS = {
    "regular": "americanfootball_nfl",
    "preseason": "americanfootball_nfl_preseason",
}

def _sport_keys() -> dict:
    """Merge defaults with any overrides from app config."""
    cfg = current_app.config.get("SPORT_KEYS") or {}
    return {**DEFAULT_SPORT_KEYS, **cfg}


def _refresh_lines_for_key(sport_key: str, *, force_week: int | None) -> tuple[int, int]:
    """
    Fetch odds for a sport key and upsert games.
    Returns (created_count, updated_count).
    """
    events = fetch_odds(sport_key)
    created = updated = 0
    for ev in events or []:
        existed = Game.query.filter_by(odds_event_id=ev.get("id")).one_or_none() is not None
        upsert_game_from_odds_event(ev, force_week=force_week)
        if existed:
            updated += 1
        else:
            created += 1
    db.session.commit()
    return created, updated


def _update_scores_recent_for_key(sport_key: str, *, days_from: int = 3) -> tuple[int, int]:
    """
    Fetch recent scores (daysFrom = 1..3) and update matching games.
    Returns number of games that changed scores.
    """
    score_events = fetch_scores(sport_key, days_from=days_from)  # your client already supports days_from
    by_id = {ev.get("id"): ev for ev in (score_events or []) if ev.get("id")}
    if not by_id:
        return 0, 0

    changed = finalized = 0
    games = Game.query.filter(Game.odds_event_id.in_(list(by_id.keys()))).all()
    for g in games:
        before = (g.final_score_home, g.final_score_away)
        update_game_scores_from_score_event(g, by_id[g.odds_event_id])
        after = (g.final_score_home, g.final_score_away)
        if before != after:
            changed += 1
        # Finalize ATS whenever both scores are present (idempotent/safe)
        if after[0] is not None and after[1] is not None:
            finalize_ats_for_game(g)
            finalized += 1

    db.session.commit()
    return changed, finalized


# ---------- LINES (POST buttons) ----------

@admin_bp.route("/refresh_lines", methods=["POST"])
@login_required
def admin_refresh_lines():
    keys = _sport_keys()
    res = refresh_lines_for_key(keys["regular"], force_week=None)  # auto-compute week
    flash(
        f"Regular-season lines refreshed for week {res['week']}: "
        f"created={res['created']}, updated={res['updated']}.",
        "success",
    )
    return redirect(url_for("admin.admin_panel", week=res["week"]))

@admin_bp.route("/refresh_lines_preseason", methods=["POST"])
@login_required
def admin_refresh_lines_preseason():
    keys = _sport_keys()
    # Preseason always stored as week 0
    res = refresh_lines_for_key(keys["preseason"], force_week=0)
    flash(
        f"Preseason lines refreshed (week 0): created={res['created']}, updated={res['updated']}.",
        "success",
    )
    return redirect(url_for("admin.admin_panel", week=0))


# ---------- SCORES (POST buttons) ----------

@admin_bp.route("/update_scores_regular", methods=["POST"])
@login_required
def admin_update_scores_regular():
    keys = _sport_keys()
    changed, finalized = _update_scores_recent_for_key(keys["regular"], days_from=3)
    flash(f"Regular-season scores updated (last 3 days). Changed: {changed}. ATS finalized: {finalized}.", "success")
    return redirect(url_for("admin.admin_panel", week=request.args.get("week", type=int)))

@admin_bp.route("/update_scores_preseason", methods=["POST"])
@login_required
def admin_update_scores_preseason():
    keys = _sport_keys()
    changed, finalized = _update_scores_recent_for_key(keys["preseason"], days_from=3)
    flash(f"Preseason scores updated (last 3 days). Changed: {changed}. ATS finalized: {finalized}.", "success")
    return redirect(url_for("admin.admin_panel", week=request.args.get("week", type=int)))

@admin_bp.route("/lock_current_week", methods=["POST"])
@login_required
def admin_lock_current_week():
    wk = request.args.get('week', type=int)  # respects the UI selection
    season_type = "preseason" if wk == 0 else "regular"
    res = lock_current_week(week=wk, season_type=season_type)

    if res.get('locked_week') is not None:
        locked_games = Game.query.filter_by(week=res['locked_week'], spread_is_locked=True).all()
        for g in locked_games:
            snapshot_closing_lines_for_game(g, line_source="Admin/Lock")
        db.session.commit()

    flash(
        f"Locked week {res.get('locked_week')} — games locked={res.get('games_locked')}. "
        f"Closing lines snapshotted.",
        "success",
    )
    return redirect(url_for("admin.admin_panel", week=res.get('locked_week')))

@admin_bp.route("/panel", methods=["GET"])
@login_required
def admin_panel():
    # Week options
    week_rows = db.session.query(Game.week).distinct().order_by(Game.week.asc()).all()
    weeks: list[int] = [w for (w,) in week_rows] or [0]

    # Figure out the "current" week by kickoff date
    now = datetime.now(timezone.utc)
    current_week = (
        db.session.query(Game.week)
        .filter(Game.kickoff_at <= now)
        .order_by(Game.week.desc())
        .limit(1)
        .scalar()
    )
    if current_week is None:
        current_week = 0

    # Selected week from query param
    selected_week = request.args.get("week", type=int)
    if selected_week is None:
        # default to current if it’s in weeks, else 0
        selected_week = current_week if current_week in weeks else 0
    elif selected_week not in weeks:
        # if param isn’t valid, force to 0
        selected_week = 0

    # Pull all picks for the selected week
    rows = (
        db.session.query(Pick, User, Game)
        .join(Game, Pick.game_id == Game.id)
        .join(User, Pick.user_id == User.id)
        .filter(Game.week == selected_week)
        .order_by(User.username.asc(), Game.kickoff_at.asc(), Game.id.asc())
        .all()
    )

    # user_id -> {"username": str, "picks": [{"team": str, "status": str}]}
    by_user: Dict[int, Dict] = defaultdict(lambda: {"user_id": None, "username": "", "picks": []})

    for p, u, g in rows:
        pts = points_for_pick(p, g)  # float | None
        status = "pending" if pts is None else ("win" if pts == 1.0 else "push" if pts == 0.5 else "loss")
        slot = by_user[u.id]
        slot["user_id"] = u.id
        slot["username"] = u.username
        slot["picks"].append({"team": p.chosen_team, "status": status})

    # Ensure all users appear; pad to 5 cells; compute counts; sort users
    all_users = User.query.order_by(User.username.asc()).all()
    matrix: List[Dict] = []
    for u in all_users:
        rec = by_user.get(u.id) or {"user_id": u.id, "username": u.username, "picks": []}
        picks_list = list(rec.get("picks", []))[:5]
        while len(picks_list) < 5:
            picks_list.append({"team": "", "status": "empty"})
        rec["picks"] = picks_list
        rec["pick_count"] = sum(1 for pk in picks_list if pk["team"])
        matrix.append(rec)

    # Show users with picks first, then alpha
    matrix.sort(key=lambda r: (-r["pick_count"], r["username"].lower()))

    # ----- ATS SUMMARY (season-to-date or single week) -----
    ats_scope = request.args.get("ats_scope", "season")  # "season" or "week"

    # Build a grouped query: per team, count COVER / PUSH / NO_COVER up to the selected week
    covers = db.func.sum(db.case((TeamGameATS.ats_result == 'COVER', 1), else_=0))
    pushes = db.func.sum(db.case((TeamGameATS.ats_result == 'PUSH', 1), else_=0))
    nocovs = db.func.sum(db.case((TeamGameATS.ats_result == 'NO_COVER', 1), else_=0))

    q = (
        db.session.query(
            TeamGameATS.team.label("team"),
            covers.label("covers"),
            pushes.label("pushes"),
            nocovs.label("nocovers"),
        )
        .join(Game, TeamGameATS.game_id == Game.id)
    )

    if ats_scope == "week":
        q = q.filter(Game.week == selected_week)
    else:
        # season-to-date up through selected_week
        q = q.filter(Game.week != None, Game.week <= selected_week)

    q = q.group_by(TeamGameATS.team).order_by(TeamGameATS.team.asc())
    ats_rows = q.all()

    ats_summary = []
    for r in ats_rows:
        total = (r.covers or 0) + (r.pushes or 0) + (r.nocovers or 0)
        pct = (float(r.covers) / total * 100.0) if total else 0.0
        ats_summary.append({
            "team": r.team,
            "covers": int(r.covers or 0),
            "pushes": int(r.pushes or 0),
            "nocovers": int(r.nocovers or 0),
            "total": total,
            "pct": pct,  # cover %
            "record": f"{int(r.covers or 0)}-{int(r.nocovers or 0)}-{int(r.pushes or 0)}",
        })

    return render_template(
        "admin_panel.html",
        weeks=weeks,
        selected_week=selected_week,
        matrix=matrix,
        ats_scope=ats_scope,       
        ats_summary=ats_summary,
    )





@admin_bp.route("/lines/fragment")
@login_required
def admin_lines_fragment():
    """Admin-only preview of lines for ANY week (ignores locking)."""
    week = request.args.get("week", type=int) or 0
    tzname = request.args.get("tz")

    games = (Game.query
                .filter(Game.week == week)
                .order_by(Game.kickoff_at.asc(), Game.id.asc())
                .all())

    # build ats_by_game map (game_id, team) -> ats_result
    game_ids = [g.id for g in games]
    ats_rows = []
    if game_ids:
        ats_rows = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all()
    ats_by_game = { (r.game_id, r.team): (r.ats_result or None) for r in ats_rows }

    # group by day/time (same as your public fragment)
    days: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in games:
        day_title, day_sort = _day_key(g.kickoff_at, tzname)
        times = days.setdefault((day_title, day_sort), OrderedDict())
        time_title, time_sort = _time_key(g.kickoff_at, tzname)
        times.setdefault((time_title, time_sort), []).append(g)

    groups = []
    for (day_title, _), times in days.items():
        groups.append((day_title, [(t[0], items) for t, items in times.items()]))

    # Admin preview is read-only; don’t show pick checkboxes
    return render_template(
        "partials/_weekly_lines_list.html",
        groups=groups,
        picks_by_game={},                  # not needed for preview
        now_utc=datetime.now(timezone.utc),
        disable_inputs=True,               # ⬅️ tells the partial to hide inputs
        ats_by_game=ats_by_game,
    )

@admin_bp.route("/ats/snapshot_locked", methods=["POST"])
@login_required
def admin_ats_snapshot_locked():
    games = Game.query.filter_by(spread_is_locked=True).all()
    for g in games:
        snapshot_closing_lines_for_game(g, line_source="Admin/Button")
    db.session.commit()
    flash(f"ATS closing spreads snapshotted for {len(games)} locked games.", "success")
    return redirect(url_for("admin.admin_panel", week=request.args.get("week", type=int)))

@admin_bp.route("/ats/backfill", methods=["POST"])
@login_required
def admin_ats_backfill():
    games = Game.query.all()
    snap = fin = 0
    for g in games:
        if getattr(g, 'spread_is_locked', False):
            snapshot_closing_lines_for_game(g, line_source="Admin/Backfill")
            snap += 1
        if g.final_score_home is not None and g.final_score_away is not None:
            finalize_ats_for_game(g)
            fin += 1
    db.session.commit()
    flash(f"ATS backfill complete — snapshots={snap}, finalized={fin}", "success")
    return redirect(url_for("admin.admin_panel", week=request.args.get("week", type=int)))