# app/admin/routes.py
from __future__ import annotations

from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify, abort, current_app
from flask_login import login_required, current_user
from datetime import datetime, timezone, date
from collections import defaultdict, OrderedDict
from typing import List, Dict, Tuple, Any
from urllib.parse import urlencode
from sqlalchemy.sql import sqltypes as T

from app.extensions import db
from app.models import Game, Pick, User, TeamGameATS
from app.scoring import points_for_pick
from app.services.time_utils import day_key, time_key
from app.filters import abbr_team, team_short  # chips

# Services
from app.services.games_sync import (
    import_all_lines,
    import_all_scores,
    lock_weeks_through_current,
    refresh_spreads_unlocked,
)
from app.services.week import current_week_number
from app.services.ats import snapshot_closing_lines_for_game, finalize_ats_for_game

from . import bp  # use the blueprint from __init__.py


# ------------------------------------------------------------
# HUB (Collection Page)
# ------------------------------------------------------------
@bp.get("/")
@login_required
def index():
    """Lightweight admin hub (collection page)."""
    # minimal context for lines preview only
    week_rows = db.session.query(Game.week).distinct().order_by(Game.week.asc()).all()
    weeks: List[int] = [w for (w,) in week_rows] or [0]
    current_wk = current_week_number()
    selected_week = request.args.get("week", type=int)
    if selected_week is None:
        selected_week = current_wk if current_wk in weeks else (weeks[-1] if weeks else 0)
    elif selected_week not in weeks and weeks:
        selected_week = weeks[-1]

    return render_template("admin_panel.html", weeks=weeks, selected_week=selected_week)


# ------------------------------------------------------------
# ACTION BUTTONS PAGE (hide all ops behind this card)
# ------------------------------------------------------------
@bp.get("/actions")
@login_required
def actions():
    week_rows = db.session.query(Game.week).distinct().order_by(Game.week.asc()).all()
    weeks: List[int] = [w for (w,) in week_rows] or [0]
    current_wk = current_week_number()
    selected_week = request.args.get("week", type=int)
    if selected_week is None:
        selected_week = current_wk if current_wk in weeks else (weeks[-1] if weeks else 0)
    elif selected_week not in weeks and weeks:
        selected_week = weeks[-1]
    return render_template("actions.html", weeks=weeks, selected_week=selected_week)


# actions (POST) stay the same, just redirect to admin.actions
@bp.post("/import-lines")
@login_required
def admin_import_lines():
    res = import_all_lines()
    flash(
        f"Lines imported: created={res['created']}, updated={res['updated']}, skipped_locked={res['skipped_locked']}.",
        "success",
    )
    return redirect(url_for("admin.actions", week=request.args.get("week", type=int)))

@bp.post("/import-scores")
@login_required
def admin_import_scores():
    days_from = request.args.get("days_from", type=int) or 3
    res = import_all_scores(days_from=days_from)
    flash(
        f"Scores updated (daysFrom={days_from}): updated={res['updated_scores']}, unchanged={res['unchanged']}, missing_game={res['missing_game']}.",
        "success",
    )
    return redirect(url_for("admin.actions", week=request.args.get("week", type=int)))

@bp.post("/lock-weeks")
@login_required
def admin_lock_weeks():
    res = lock_weeks_through_current()
    locked_games = Game.query.filter_by(spread_is_locked=True).all()
    for g in locked_games:
        snapshot_closing_lines_for_game(g, line_source="Admin/Lock")
    db.session.commit()
    flash(
        f"Weeks locked through {res['week_now']}. Newly locked games={res['locked']}. Closing lines snapshotted.",
        "success",
    )
    return redirect(url_for("admin.actions", week=res.get("week_now")))

@bp.post("/refresh-spreads")
@login_required
def admin_refresh_spreads():
    res = refresh_spreads_unlocked()
    flash(
        f"Spreads refreshed (UNLOCKED only): created={res['created']}, updated={res['updated']}, skipped_locked={res['skipped_locked']}.",
        "success",
    )
    return redirect(url_for("admin.actions", week=request.args.get("week", type=int)))

@bp.post("/prep-week")
@login_required
def admin_prep_week():
    week = request.args.get("week", type=int) or request.form.get("week", type=int)
    if week is None:
        flash("No week specified.", "error")
        return redirect(url_for("admin.actions"))
    games = Game.query.filter(Game.week == week).all()
    locked_now = 0
    for g in games:
        if not getattr(g, "spread_is_locked", False):
            g.spread_is_locked = True
            locked_now += 1
    snap = 0
    for g in games:
        if getattr(g, "spread_is_locked", False):
            snapshot_closing_lines_for_game(g, line_source="Admin/PrepWeek")
            snap += 1
    db.session.commit()
    flash(f"Week {week} prepped — locked {locked_now}, snapshots {snap}.", "success")
    return redirect(url_for("admin.actions", week=week))

@bp.post("/scores-finalize")
@login_required
def admin_scores_and_finalize_week():
    week = request.args.get("week", type=int) or request.form.get("week", type=int)
    days_from = request.args.get("days_from", type=int) or 3
    if week is None:
        flash("No week specified.", "error")
        return redirect(url_for("admin.actions"))
    res_scores = import_all_scores(days_from=days_from)
    games = Game.query.filter(Game.week == week).all()
    fin = 0
    for g in games:
        if g.final_score_home is not None and g.final_score_away is not None:
            finalize_ats_for_game(g); fin += 1
    db.session.commit()
    flash(f"Week {week}: scores updated (d{days_from}); ATS finalized for {fin} games.", "success")
    return redirect(url_for("admin.actions", week=week))


# ------------------------------------------------------------
# ATS SUMMARY PAGE
# ------------------------------------------------------------
@bp.get("/ats")
@login_required
def ats_summary():
    week_rows = db.session.query(Game.week).distinct().order_by(Game.week.asc()).all()
    weeks: List[int] = [w for (w,) in week_rows] or [0]
    current_wk = current_week_number()
    selected_week = request.args.get("week", type=int)
    if selected_week is None:
        selected_week = current_wk if current_wk in weeks else (weeks[-1] if weeks else 0)
    elif selected_week not in weeks and weeks:
        selected_week = weeks[-1]

    ats_scope = request.args.get("ats_scope", "season")  # "season" or "week"

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
            "pct": pct,
            "record": f"{int(r.covers or 0)}-{int(r.nocovers or 0)}-{int(r.pushes or 0)}",
        })

    return render_template(
        "ats.html",
        weeks=weeks,
        selected_week=selected_week,
        ats_scope=ats_scope,
        ats_summary=ats_summary,
    )


# ------------------------------------------------------------
# PICKS MATRIX PAGE (optional, if you want it split out)
# ------------------------------------------------------------
@bp.get("/picks")
@login_required
def picks_matrix():
    week_rows = db.session.query(Game.week).distinct().order_by(Game.week.asc()).all()
    weeks: List[int] = [w for (w,) in week_rows] or [0]
    current_wk = current_week_number()
    selected_week = request.args.get("week", type=int)
    if selected_week is None:
        selected_week = current_wk if current_wk in weeks else (weeks[-1] if weeks else 0)
    elif selected_week not in weeks and weeks:
        selected_week = weeks[-1]

    rows = (
        db.session.query(Pick, User, Game)
        .join(Game, Pick.game_id == Game.id)
        .join(User, Pick.user_id == User.id)
        .filter(Game.week == selected_week)
        .order_by(User.username.asc(), Game.kickoff_at.asc(), Game.id.asc())
        .all()
    )
    by_user: Dict[int, Dict] = defaultdict(lambda: {"user_id": None, "username": "", "picks": []})
    for p, u, g in rows:
        pts = points_for_pick(p, g)
        status = "pending" if pts is None else ("win" if pts == 1.0 else "push" if pts == 0.5 else "loss")
        slot = by_user[u.id]
        slot["user_id"] = u.id
        slot["username"] = u.username
        slot["picks"].append({"team": p.chosen_team, "status": status})

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

    matrix.sort(key=lambda r: (-r["pick_count"], r["username"].lower()))

    return render_template("picks.html", weeks=weeks, selected_week=selected_week, matrix=matrix)


# ------------------------------------------------------------
# Email previews page (links to previews)
# ------------------------------------------------------------
@bp.get("/email/previews")
@login_required
def email_previews():
    week = request.args.get("week", type=int) or current_week_number()
    # You already have context-building helpers for spreads (groups, etc.)
    # For now, just render a page that links out to the previews.

    return render_template(
        "email_previews.html",
        selected_week=week,
    )

@bp.get("/email/weekly-standings/preview", endpoint="preview_weekly_standings_email")
@login_required
def preview_weekly_standings_email():
    """Temporary placeholder until the real standings email is built."""
    week = request.args.get("week", type=int) or current_week_number()
    # You can swap this to render a real template later.
    return render_template(
        "email/weekly_standings.html",
        week_number=week,
    )

@bp.get("/email/weekly-spreads/preview")
@login_required
def preview_weekly_spreads_email():
    week = request.args.get("week", type=int) or current_week_number()

    games = (
        db.session.query(Game)
        .filter(Game.week == week)
        .order_by(Game.kickoff_at.asc(), Game.id.asc())
        .all()
    )
    groups = _group_games_for_email(games)

    prev_week = max(1, week - 1)
    standings_rows = _build_standings_rows_for_email(prev_week)

    return render_template(
        "email/weekly_spreads.html",
        groups=groups,
        all_locked=True,
        now_utc=datetime.now(timezone.utc),
        week_number=week,
        week_date_range_text="",
        weekly_lines_url=url_for("weekly_lines.weekly_lines", week=week, _external=True),
        timezone_name="MT",
        current_year=datetime.now().year,
        standings_rows=standings_rows,
        prev_week_number=prev_week,
    )

@bp.get("/email/weekly-spreads/preview.txt")
@login_required
def preview_weekly_spreads_email_txt():
    """Plain-text preview (multipart/alternative text part)."""
    week = request.args.get("week", type=int) or current_week_number()
    games = (
        db.session.query(Game)
        .filter(Game.week == week)
        .order_by(Game.kickoff_at.asc(), Game.id.asc())
        .all()
    )
    groups = _group_games_for_email(games)
    context = {
        "groups": groups,
        "all_locked": True,
        "now_utc": datetime.now(timezone.utc),
        "ats_resolved": {},
        "week_number": week,
        "week_date_range_text": "",
        "weekly_lines_url": url_for("weekly_lines.weekly_lines", week=week, _external=True),
        "about_url": url_for("standings.standings", _external=True),
        "timezone_name": "MT",
        "current_year": datetime.now().year,
    }
    return render_template("email/weekly_spreads.txt", **context), 200, {"Content-Type": "text/plain; charset=utf-8"}

def get_tzname() -> str:
    tz = getattr(current_user, "timezone", None)
    return tz or "MT"

def _to_sort_tuple(x: Any) -> tuple[int, Any]:
    """
    Normalize various potential sort-key types (date/datetime/str/None/number)
    into a single comparable tuple. Lower tuple compares first.
    Priority order:
      0: datetime-like
      1: numeric
      2: string
      9: None / unknown
    """
    if isinstance(x, datetime):
        # sort by actual datetime
        return (0, x)
    if isinstance(x, date):
        # convert date to datetime at midnight for stable ordering
        return (0, datetime(x.year, x.month, x.day, tzinfo=timezone.utc))
    if isinstance(x, (int, float)):
        return (1, x)
    if isinstance(x, str):
        return (2, x)
    if x is None:
        return (9, 0)
    # fallback to string representation
    return (2, str(x))

def _min_sort(a: tuple[int, Any] | None, b: Any) -> tuple[int, Any]:
    """Return the min (normalized) of existing tuple vs new raw value."""
    nb = _to_sort_tuple(b)
    if a is None:
        return nb
    return a if a <= nb else nb

def _group_games_for_email(games, tzname: str = "America/Denver"):
    """
    Returns the same structure your weekly_lines partial uses:
    groups = [(day_title, [(time_title, [games])])]
    """
    by_day = {}
    day_order = {}

    for g in games:
        dlabel, dsort = day_key(g.kickoff_at, tzname)
        if dlabel not in by_day:
            by_day[dlabel] = []
            day_order[dlabel] = dsort
        by_day[dlabel].append(g)

    groups = []
    for dlabel in sorted(by_day.keys(), key=lambda d: day_order[d]):
        day_games = sorted(by_day[dlabel], key=lambda gg: (gg.kickoff_at or datetime.max.replace(tzinfo=timezone.utc), gg.id))

        by_time = {}
        time_order = {}

        for g in day_games:
            tlabel, tsort = time_key(g.kickoff_at, tzname)
            if tlabel not in by_time:
                by_time[tlabel] = []
                time_order[tlabel] = tsort
            by_time[tlabel].append(g)

        times = [(t, by_time[t]) for t in sorted(by_time.keys(), key=lambda t: time_order[t])]
        groups.append((dlabel, times))

    return groups

def _build_standings_rows_for_email(prev_week: int):
    """
    Returns rows for the email standings partial:
      {
        "rank": int,
        "name": str,                 # display name using First / First L.
        "picks": [{"label": str, "result": "W|L|P|pending|empty"}],  # label = nickname
        "week_w": int, "week_l": int, "week_p": int,
        "total_w": int, "total_l": int, "total_p": int,
        "points": float
      }
    """
    from collections import Counter  # local import to avoid clutter at top

    users = User.query.order_by(User.username.asc()).all()

    # ---------- display-name logic (same idea as your standings route) ----------
    def split_name(u: User) -> tuple[str, str | None]:
        first = (getattr(u, "first_name", None) or "").strip()
        last  = (getattr(u, "last_name",  None) or "").strip()
        if not first:
            first = (u.username or "").strip()
        last_initial = last[0].upper() if last else None
        return first, last_initial

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

    # ---------- weekly picks for prev_week ----------
    pairs_week = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.week == prev_week)
        .all()
    )
    by_user_week: Dict[int, List[Tuple[Pick, Game]]] = {}
    for p, g in pairs_week:
        by_user_week.setdefault(p.user_id, []).append((p, g))

    game_ids_week = [g.id for _, g in pairs_week]
    ats_rows_week = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids_week)).all() if game_ids_week else []
    ats_by_game_week = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows_week}

    # ---------- season totals through prev_week ----------
    pairs_to_date = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.week <= prev_week)
        .all()
    )
    by_user_to_date: Dict[int, List[Tuple[Pick, Game]]] = {}
    for p, g in pairs_to_date:
        by_user_to_date.setdefault(p.user_id, []).append((p, g))

    game_ids_to_date = [g.id for _, g in pairs_to_date]
    ats_rows_to_date = (
        TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids_to_date)).all()
        if game_ids_to_date else []
    )
    ats_by_game_to_date = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows_to_date}

    # ---------- helpers ----------
    def is_final(g: Game) -> bool:
        comp = getattr(g, "completed", None)
        if comp is not None:
            return bool(comp)
        return (g.final_score_home is not None and g.final_score_away is not None)

    def grade(p: Pick, g: Game, pref_ats: dict | None) -> str:
        # 'W'|'L'|'P'|'pending' — prefer ATS if present
        if pref_ats is not None:
            ats = pref_ats.get((g.id, p.chosen_team))
            if ats == "COVER":    return "W"
            if ats == "NO_COVER": return "L"
            if ats == "PUSH":     return "P"
        if not is_final(g):
            return "pending"
        pts = points_for_pick(p, g)
        if pts == 1.0:  return "W"
        if pts == 0.5:  return "P"
        if pts == 0.0:  return "L"
        return "pending"

    rows = []
    for u in users:
        display_name = display_name_for(u)

        weekly_w = weekly_l = weekly_p = 0
        picks_disp = []
        weekly_pairs = sorted(
            by_user_week.get(u.id, []),
            key=lambda pg: (pg[1].kickoff_at or datetime.max.replace(tzinfo=timezone.utc)),
        )

        for p, g in weekly_pairs[:5]:
            res = grade(p, g, ats_by_game_week)
            if res == "W": weekly_w += 1
            elif res == "L": weekly_l += 1
            elif res == "P": weekly_p += 1
            # nickname only (e.g., "Chargers", not "Los Angeles Chargers")
            nick = team_short(p.chosen_team) or p.chosen_team or ""
            picks_disp.append({"label": nick, "result": res})

        while len(picks_disp) < 5:
            picks_disp.append({"label": "", "result": "empty"})

        tot_w = tot_l = tot_p = 0
        points = 0.0
        for p, g in by_user_to_date.get(u.id, []):
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
            points += float(pts)
            if pts == 1.0:   tot_w += 1
            elif pts == 0.5: tot_p += 1
            elif pts == 0.0: tot_l += 1

        rows.append({
            "rank": 0,  # set after sort
            "name": display_name,         # ✅ display-name logic applied
            "picks": picks_disp,          # ✅ nickname labels
            "week_w": weekly_w, "week_l": weekly_l, "week_p": weekly_p,
            "total_w": tot_w, "total_l": tot_l, "total_p": tot_p,
            "points": points,
        })

    # Same sort as your standings page
    rows.sort(key=lambda r: (-r["points"], -r["total_w"], r["total_l"], -r["total_p"], r["name"].lower()))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


# ------------------------------------------------------------
# Admin-only lines fragment (read-only)
# ------------------------------------------------------------
@bp.get("/lines/fragment")
@login_required
def admin_lines_fragment():
    week = request.args.get("week", type=int) or 0
    tzname = (request.args.get("tz") or "").strip() or "UTC"

    try:
        current_app.logger.info("[admin_lines_fragment] start week=%s tz=%s", week, tzname)

        games = (
            Game.query
            .filter(Game.week == week)
            .order_by(Game.kickoff_at.asc(), Game.id.asc())
            .all()
        )
        current_app.logger.info("[admin_lines_fragment] games=%d", len(games))

        game_ids = [g.id for g in games]
        ats_rows = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all() if game_ids else []
        current_app.logger.info("[admin_lines_fragment] ats_rows=%d", len(ats_rows))

        # Build a quick lookup for home/away by game id
        by_id = {g.id: g for g in games}

        # Normalize ATS → 'W' | 'L' | 'P'
        to_wlp = {'COVER': 'W', 'NO_COVER': 'L', 'PUSH': 'P'}
        ats_resolved = {}  # {(game_id, 'home'|'away'): 'W'|'L'|'P'|None}

        for r in ats_rows:
            g = by_id.get(r.game_id)
            if not g:
                continue
            raw = (r.ats_result or '').upper()
            wlp = to_wlp.get(raw) if raw else None
            if r.team == g.home_team:
                ats_resolved[(g.id, 'home')] = wlp
            elif r.team == g.away_team:
                ats_resolved[(g.id, 'away')] = wlp
            # else: ignore unexpected team string

        # Group games by day/time (uses tz)
        from collections import OrderedDict
        days = OrderedDict()
        for g in games:
            day_title, day_sort = day_key(g.kickoff_at, tzname)
            times = days.setdefault((day_title, day_sort), OrderedDict())
            time_title, time_sort = time_key(g.kickoff_at, tzname)
            times.setdefault((time_title, time_sort), []).append(g)

        groups = []
        for (day_title, _), times in days.items():
            groups.append((day_title, [(t[0], items) for t, items in times.items()]))

        current_app.logger.info("[admin_lines_fragment] groups=%d", len(groups))

        html = render_template(
            "partials/_weekly_lines_list.html",
            groups=groups,
            picks_by_game={},                   # read-only preview in admin
            now_utc=datetime.now(timezone.utc),
            disable_inputs=True,
            ats_resolved=ats_resolved,          # ✅ what the partial expects
            tzname=tzname,
            abbr_team=abbr_team,
            all_locked=False,       # admin view should not require lock
            admin_preview=True,     # ✅ lets spreads show up
        )
        current_app.logger.info("[admin_lines_fragment] render OK")
        return html

    except Exception:
        current_app.logger.exception("[admin_lines_fragment] failed week=%s tz=%s", week, tzname)
        return "Fragment error", 500

# ------------------------------------------------------------
# DB Manager (unchanged from earlier message)
# ------------------------------------------------------------
MODEL_MAP = {
    "users": User,
    "games": Game,
    "picks": Pick,
    "ats": TeamGameATS,
}

def _get_model_or_404(name: str):
    m = MODEL_MAP.get(name.lower())
    if not m:
        abort(404)
    return m

@bp.get("/db")
@login_required
def db_home():
    return render_template("db_home.html", model_names=sorted(MODEL_MAP.keys()))

@bp.get("/db/<model_name>")
@login_required
def db_table(model_name):
    Model = _get_model_or_404(model_name)
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, int(request.args.get("per_page", 25)))

    # All columns + primary key
    cols = [c.name for c in Model.__table__.columns]
    pk = list(Model.__table__.primary_key.columns)[0].name

    # -------- column kind detection (for rendering + parsing) --------
    def kind_for(col):
        t = col.type
        if isinstance(t, (T.String, T.Text, T.Unicode, T.UnicodeText)):
            return "text"
        if isinstance(t, T.Boolean):
            return "bool"
        if isinstance(t, (T.Integer, T.BigInteger, T.SmallInteger)):
            return "int"
        if isinstance(t, (T.Numeric, T.Float, T.DECIMAL)):
            return "num"
        if isinstance(t, T.DateTime):
            return "dt"
        if isinstance(t, T.Date):
            return "date"
        return "other"

    col_kinds = {c.name: kind_for(c) for c in Model.__table__.columns}

    # -------- base query + global search --------
    query = Model.query
    q = request.args.get("q")
    if q:
        like = f"%{q}%"
        or_clauses = []
        for c in Model.__table__.columns:
            if col_kinds[c.name] == "text":
                or_clauses.append(getattr(Model, c.name).ilike(like))
        if or_clauses:
            from sqlalchemy import or_
            query = query.filter(or_(*or_clauses))

    # -------- per-column filters (f_<col>, f_<col>_min, f_<col>_max) --------
    def parse_bool(v: str) -> bool | None:
        if v is None or v == "":
            return None
        v = str(v).strip().lower()
        if v in ("1", "true", "t", "yes", "y", "on"):
            return True
        if v in ("0", "false", "f", "no", "n", "off"):
            return False
        return None

    fvals = {}
    for c in cols:
        k = col_kinds[c]
        col = getattr(Model, c)

        val  = request.args.get(f"f_{c}", "")
        vmin = request.args.get(f"f_{c}_min", "")
        vmax = request.args.get(f"f_{c}_max", "")

        fvals[c] = {"kind": k, "val": val, "min": vmin, "max": vmax}

        if k == "text":
            if val:
                query = query.filter(col.ilike(f"%{val}%"))
        elif k in ("int", "num"):
            # exact value if provided
            if val:
                try:
                    n = float(val) if k == "num" else int(val)
                    query = query.filter(col == n)
                except Exception:
                    pass
            # range if provided
            if vmin:
                try:
                    n = float(vmin) if k == "num" else int(vmin)
                    query = query.filter(col >= n)
                except Exception:
                    pass
            if vmax:
                try:
                    n = float(vmax) if k == "num" else int(vmax)
                    query = query.filter(col <= n)
                except Exception:
                    pass
        elif k == "bool":
            b = parse_bool(val)
            if b is not None:
                # .is_(True/False) is correct for boolean
                query = query.filter(col.is_(b))
        elif k in ("dt", "date"):
            def _parse_dt(s: str):
                if not s:
                    return None
                try:
                    return datetime.fromisoformat(s)
                except Exception:
                    return None
            def _parse_d(s: str):
                if not s:
                    return None
                try:
                    return date.fromisoformat(s)
                except Exception:
                    return None

            if k == "dt":
                dmin = _parse_dt(vmin); dmax = _parse_dt(vmax)
            else:
                dmin = _parse_d(vmin);  dmax = _parse_d(vmax)

            if dmin is not None:
                query = query.filter(col >= dmin)
            if dmax is not None:
                query = query.filter(col <= dmax)
        else:
            # unknown types: fall back to substring match if value provided
            if val:
                try:
                    query = query.filter(col.ilike(f"%{val}%"))
                except Exception:
                    pass

    # -------- sorting --------
    sort = request.args.get("sort") or pk
    dir_ = request.args.get("dir", "asc").lower()
    if sort not in cols:
        sort = pk
    col_obj = getattr(Model, sort)
    if dir_ == "desc":
        query = query.order_by(col_obj.desc())
    else:
        dir_ = "asc"
        query = query.order_by(col_obj.asc())

    # paginate AFTER filtering/sorting
    page_obj = query.paginate(page=page, per_page=per_page, error_out=False)
    rows = page_obj.items

    # build a preserved query string without sort/dir/page (for header links)
    preserved = {k: v for k, v in request.args.items()}
    for k in ("sort", "dir", "page"):
        preserved.pop(k, None)
    base_qs = urlencode(preserved)

    return render_template(
        "db_table.html",
        model_name=model_name,
        cols=cols,
        rows=rows,
        pk=pk,
        page=page_obj.page,
        pages=page_obj.pages or 1,
        per_page=per_page,
        q=q or "",
        sort=sort,
        dir=dir_,
        col_kinds=col_kinds,
        fvals=fvals,
        base_qs=base_qs,
    )


@bp.patch("/db/<model_name>/<int:row_id>")
@login_required
def db_update_cell(model_name, row_id):
    data = request.get_json(force=True, silent=True) or {}
    field = data.get("field"); value = data.get("value")
    if not field:
        return jsonify({"ok": False, "error": "Missing field"}), 400

    Model = _get_model_or_404(model_name)
    obj = Model.query.get_or_404(row_id)
    if field not in Model.__table__.columns:
        return jsonify({"ok": False, "error": f"Unknown field '{field}'"}), 400

    col = Model.__table__.columns[field]
    try:
        pytype = col.type.python_type
        if value is None or value == "":
            casted = None
        elif pytype is bool:
            casted = str(value).lower() in ("1","true","t","yes","y","on")
        else:
            casted = pytype(value)
    except Exception:
        casted = value

    setattr(obj, field, casted)
    db.session.add(obj)
    db.session.commit()
    return jsonify({"ok": True})
