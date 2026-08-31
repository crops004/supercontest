# app/admin/routes.py
from __future__ import annotations

from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify, abort, current_app
from flask_login import login_required
from datetime import datetime, timezone
from collections import defaultdict, OrderedDict
from typing import List, Dict

from app.extensions import db
from app.models import Game, Pick, User, TeamGameATS
from app.scoring import points_for_pick
from app.filters import abbr_team  # chips

# Services
from app.services.games_sync import (
    import_all_lines,
    import_all_scores,
    lock_weeks_through_current,
    refresh_spreads_unlocked,
)
from app.services.week import current_week_number
from app.services.ats import snapshot_closing_lines_for_game, finalize_ats_for_game
from app.services.time_utils import day_key, time_key
from app.services.picks import remaining_picks_this_week

from . import bp  # use the blueprint from __init__.py

# NOTE: email preview/send routes live in app/admin/emails.py, and the
# generic DB browser/editor lives in app/admin/db_admin.py. Both modules are
# imported by app/admin/__init__.py so their routes register on this same
# blueprint.


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
    # ---- figure out which week to show, same logic you already had ----
    week_rows = db.session.query(Game.week).distinct().order_by(Game.week.asc()).all()
    weeks: list[int] = [w for (w,) in week_rows] or [0]
    current_wk = current_week_number()

    selected_week = request.args.get("week", type=int)
    if selected_week is None:
        selected_week = current_wk if current_wk in weeks else (weeks[-1] if weeks else 0)
    elif selected_week not in weeks and weeks:
        selected_week = weeks[-1]

    # ---- counts for buttons ----
    # weekly email recipients (opt-in flag you already use)
    recap_count = (
        User.query
            .filter(User.email.isnot(None))
            .filter(User.notify_weekly_recap.is_(True))
            .count()
    )

    # picks reminder recipients: users who (a) have email, (b) opted in (notify_picks_reminder),
    # and (c) still have remaining picks for selected_week.
    picks_per_week = int(current_app.config.get("PICKS_PER_WEEK", 5))

    # pull candidates in one query…
    candidates = (
        User.query
            .filter(User.email.isnot(None))
            .filter(User.notify_picks_reminder.is_(True))   # make sure you've added this column/migration
            .all()
    )

    reminder_count = 0
    for u in candidates:
        remaining, wk = remaining_picks_this_week(u.id, picks_per_week)
        if wk == selected_week and remaining and remaining > 0:
            reminder_count += 1

    return render_template(
        "actions.html",
        weeks=weeks,
        selected_week=selected_week,
        recap_count=recap_count,
        reminder_count=reminder_count,
    )

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

# --- helpers -------------------------------------------------

def _lock_and_snapshot_week(week: int, *, line_source: str) -> tuple[int, int]:
    """
    Locks all games in `week` (if not already), then snapshots closing lines for all locked games in that week.
    Returns (locked_now_count, snap_count).
    """
    games = Game.query.filter(Game.week == week).all()

    locked_now = 0
    for g in games:
        if not getattr(g, "spread_is_locked", False):
            g.spread_is_locked = True
            locked_now += 1

    snap = 0
    for g in games:
        if getattr(g, "spread_is_locked", False):
            snapshot_closing_lines_for_game(g, line_source=line_source)
            snap += 1

    db.session.commit()
    return locked_now, snap


# --- TUESDAY MIDDAY: refresh unlocked spreads, then lock+snapshot selected week ----

@bp.post("/tuesday-lock-cycle")
@login_required
def admin_tuesday_lock_cycle():
    """
    Intended for Tuesday ~11:30 AM MDT:
      1) Refresh spreads for UNLOCKED games (any week)
      2) Lock & snapshot the selected week (new week's closing lines baseline)
    """
    week = request.args.get("week", type=int) or request.form.get("week", type=int)
    if week is None:
        flash("No week specified.", "error")
        return redirect(url_for("admin.actions"))

    res = refresh_spreads_unlocked()
    locked_now, snap = _lock_and_snapshot_week(week, line_source="Admin/TuesdayCycle")

    flash(
        f"Tuesday cycle complete — Refreshed (unlocked only): "
        f"created={res['created']} updated={res['updated']} skipped_locked={res['skipped_locked']}; "
        f"Week {week} locked={locked_now}, snapshots={snap}.",
        "success"
    )
    return redirect(url_for("admin.actions", week=week))


# --- CRON: same as above, but authenticated & time-gated --------------------------

@bp.post("/internal/cron/tuesday-lock-cycle")
def cron_tuesday_lock_cycle():
    """
    Refresh spreads for UNLOCKED games, then lock & snapshot a target week.
    Runs whenever called.

    Query params:
      - week: int (default = current_week_number())
      - dry_run: 1/true to simulate (no commit)
    """
    token = request.headers.get("X-CRON-TOKEN") or request.args.get("token")
    if not token or token != current_app.config.get("CRON_SECRET"):
        abort(401)

    week = request.args.get("week", type=int) or current_week_number()
    dry_run = str(request.args.get("dry_run", "")).strip().lower() in ("1","true","yes","y","on")

    try:
        if dry_run:
            res = refresh_spreads_unlocked()  # reads/writes spreads for unlocked; allow this preview write?
            games = Game.query.filter(Game.week == week).all()
            would_lock = sum(1 for g in games if not getattr(g, "spread_is_locked", False))
            # We *won’t* actually flip locks or snapshot in dry_run: just report
            return jsonify({
                "ok": True,
                "dry_run": True,
                "week": week,
                "refresh": res,
                "would_lock": would_lock,
                "would_snapshot": len(games),  # snapshot runs on all locked in that week
            }), 200

        res = refresh_spreads_unlocked()
        locked_now, snap = _lock_and_snapshot_week(week, line_source="Cron/TuesdayCycle")

        return jsonify({
            "ok": True,
            "week": week,
            "refresh": res,
            "locked_now": locked_now,
            "snapshots": snap,
        }), 200

    except Exception:
        current_app.logger.exception("[cron tuesday-lock-cycle] failed week=%s", week)
        return jsonify({"ok": False, "error": "tuesday_cycle_failed"}), 500

# --- helper: finalize ATS for a given week ------------------------------------

def _finalize_week_ats(week: int, *, days_from: int = 3) -> dict:
    """
    Import recent scores (past `days_from` days), then finalize ATS for all
    completed games in `week`. Returns a summary dict.
    """
    res_scores = import_all_scores(days_from=days_from)

    games = Game.query.filter(Game.week == week).all()
    finalized = 0
    for g in games:
        if g.final_score_home is not None and g.final_score_away is not None:
            finalize_ats_for_game(g)
            finalized += 1

    db.session.commit()
    return {
        "week": week,
        "updated_scores": res_scores.get("updated_scores", 0),
        "unchanged": res_scores.get("unchanged", 0),
        "missing_game": res_scores.get("missing_game", 0),
        "finalized_ats": finalized,
        "days_from": days_from,
    }


# --- CRON: finalize last week's ATS at Tue 00:00 local -------------------------

@bp.post("/internal/cron/finalize-ats")
def cron_finalize_ats():
    """
    Finalize ATS for a target week, anytime this endpoint is called.
    Defaults to LAST week (current_week_number() - 1).

    Query params:
      - week: int (override target week; default = current_week_number()-1)
      - days_from: int (default 3)
      - dry_run: 1/true to simulate and return what would happen (no commit)
    """
    token = request.headers.get("X-CRON-TOKEN") or request.args.get("token")
    if not token or token != current_app.config.get("CRON_SECRET"):
        abort(401)

    cur = current_week_number()
    default_week = max(1, (cur or 1) - 1)
    week = request.args.get("week", type=int) or default_week
    days_from = request.args.get("days_from", type=int) or 3
    dry_run = str(request.args.get("dry_run", "")).strip().lower() in ("1","true","yes","y","on")

    try:
        if dry_run:
            # Simulate score import only (don’t change DB state)
            res_scores = import_all_scores(days_from=days_from)
            games = Game.query.filter(Game.week == week).all()
            can_finalize = sum(
                1 for g in games if g.final_score_home is not None and g.final_score_away is not None
            )
            return jsonify({
                "ok": True,
                "dry_run": True,
                "week": week,
                "updated_scores": res_scores.get("updated_scores", 0),
                "unchanged": res_scores.get("unchanged", 0),
                "missing_game": res_scores.get("missing_game", 0),
                "would_finalize_ats": can_finalize,
                "days_from": days_from,
            }), 200

        summary = _finalize_week_ats(week, days_from=days_from)
        return jsonify({"ok": True, **summary}), 200

    except Exception:
        current_app.logger.exception("[cron finalize-ats] failed week=%s", week)
        return jsonify({"ok": False, "error": "finalize_failed"}), 500

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


@bp.post("/internal/cron/refresh-scores")
def cron_refresh_scores():
    token = request.headers.get("X-CRON-TOKEN") or request.args.get("token")
    if not token or token != current_app.config.get("CRON_SECRET"):
        abort(401)

    days_from = request.args.get("days_from", type=int) or 3
    try:
        res = import_all_scores(days_from=days_from)
        return jsonify({"ok": True, "days_from": days_from, **res}), 200
    except Exception:
        current_app.logger.exception("[cron refresh-scores] failed days_from=%s", days_from)
        return jsonify({"ok": False, "error": "refresh_failed"}), 500

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
