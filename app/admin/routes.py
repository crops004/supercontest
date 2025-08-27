# app/admin/routes.py
from __future__ import annotations

from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify, abort
from flask_login import login_required
from datetime import datetime, timezone, date
from collections import defaultdict, OrderedDict
from typing import List, Dict, Tuple
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
# Admin-only lines fragment (read-only)
# ------------------------------------------------------------
@bp.get("/lines/fragment")
@login_required
def admin_lines_fragment():
    week = request.args.get("week", type=int) or 0
    tzname = request.args.get("tz")

    games = Game.query.filter(Game.week == week).order_by(Game.kickoff_at.asc(), Game.id.asc()).all()
    game_ids = [g.id for g in games]
    ats_rows = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all() if game_ids else []
    ats_by_game = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows}

    days: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in games:
        day_title, day_sort = day_key(g.kickoff_at, tzname)
        times = days.setdefault((day_title, day_sort), OrderedDict())
        time_title, time_sort = time_key(g.kickoff_at, tzname)
        times.setdefault((time_title, time_sort), []).append(g)

    groups = []
    for (day_title, _), times in days.items():
        groups.append((day_title, [(t[0], items) for t, items in times.items()]))

    return render_template(
        "partials/_weekly_lines_list.html",
        groups=groups,
        picks_by_game={},
        now_utc=datetime.now(timezone.utc),
        disable_inputs=True,
        ats_by_game=ats_by_game,
        tzname=tzname,
        abbr_team=abbr_team,
    )


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
