# app/admin/emails.py
from __future__ import annotations

from datetime import datetime, timezone, date
from typing import List, Dict, Tuple, Any
from time import sleep
from zoneinfo import ZoneInfo

from flask import request, render_template, redirect, url_for, flash, jsonify, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Game, Pick, User, TeamGameATS, WeeklyEmailLog, WeeklyEmailRecipientLog
from app.scoring import points_for_pick
from app.filters import team_short
from app.emailer import send_email
from app.services.week import current_week_number
from app.services.season import current_season_id
from app.services.time_utils import day_key, time_key
from app.services.picks import remaining_picks_this_week

from . import bp


# ------------------------------------------------------------
# Email previews page (links to previews)
# ------------------------------------------------------------

# Build the exact context the weekly_spreads templates already use.
def build_weekly_spreads_context(week: int,
                                 *,
                                 locked: bool | None = None,
                                 weekly_lines_url: str | None = None) -> dict:
    games = (
        db.session.query(Game)
        .filter(Game.week == week, Game.season_id == current_season_id())
        .order_by(Game.kickoff_at.asc(), Game.id.asc())
        .all()
    )
    groups = _group_games_for_email(games)

    prev_week = max(1, week - 1)
    # No prior week to recap for the Week 1 email - skip the standings block
    # entirely instead of showing an all-zeros table.
    standings_rows = _build_standings_rows_for_email(prev_week) if week > 1 else []

    ctx = {
        "groups": groups,
        # your preview hard-coded True; allow override via `locked`
        "all_locked": True if locked is None else bool(locked),
        "now_utc": datetime.now(timezone.utc),
        "week_number": week,
        "week_date_range_text": "",
        "weekly_lines_url": weekly_lines_url or url_for("weekly_lines.weekly_lines", week=week, _external=True),
        "timezone_name": "MT",
        "current_year": datetime.now().year,
        "standings_rows": standings_rows,
        "prev_week_number": prev_week,
        "timezone_name": "MDT",              # display label only (what you want to print)
        "tzname": "America/Denver",          # IANA tz for fmt_local()
    }
    return ctx

@bp.get("/email/previews")
@login_required
def email_previews():
    row = (
        db.session.query(func.min(Game.week), func.max(Game.week))
        .filter(Game.season_id == current_season_id())
        .first()
    )

    min_raw = row[0] if row else None
    max_raw = row[1] if row else None

    min_week = int(min_raw) if min_raw is not None else 1
    max_week = int(max_raw) if max_raw is not None else min_week

    weeks = list(range(min_week, max_week + 1))

    sel = request.args.get("week", type=int) or current_week_number()
    if sel < min_week: sel = min_week
    if sel > max_week: sel = max_week

    return render_template(
        "email_previews.html",
        weeks=weeks,
        selected_week=sel,
        min_week=min_week,
        max_week=max_week,
    )

@bp.get("/email/weekly-spreads/preview")
@login_required
def preview_weekly_spreads_email():
    week = request.args.get("week", type=int) or current_week_number()
    ctx = build_weekly_spreads_context(week, locked=True)
    return render_template("email/weekly_spreads.html", **ctx)

@bp.get("/email/weekly-spreads/preview.txt")
@login_required
def preview_weekly_spreads_email_txt():
    week = request.args.get("week", type=int) or current_week_number()
    ctx = build_weekly_spreads_context(week, locked=True)
    return render_template("email/weekly_spreads.txt", **ctx), 200, {
        "Content-Type": "text/plain; charset=utf-8"
    }

@bp.get("/email/weekly-spreads/send")
@login_required
def send_weekly_spreads_email():
    """
    Use:
      /admin/email/weekly-spreads/send?week=1&to=you@gmail.com
      (optional) &locked=1 to force spreads in dev
    """
    to = request.args.get("to")
    if not to:
        return "Add ?to=you@example.com", 400

    week = request.args.get("week", type=int) or current_week_number()
    locked = request.args.get("locked")
    locked_flag = (locked == "1") if locked is not None else None

    ctx = build_weekly_spreads_context(week, locked=locked_flag)

    html_body = render_template("email/weekly_spreads.html", **ctx)
    try:
        text_body = render_template("email/weekly_spreads.txt", **ctx)
    except Exception:
        text_body = None

    try:
        send_email(
            subject=f"Week {ctx['week_number'] - 1} Results / Week {ctx['week_number']} Spreads",
            recipients=to,
            html=html_body,
            text=text_body,
        )
        return "✅ sent", 200
    except Exception as e:
        return f"❌ failed: {e}", 500

@bp.post("/email/weekly-spreads/send-all")
@login_required
def send_weekly_spreads_bulk():
    """
    Sends the Week N email to all users who have notify_weekly_recap = True.
    Uses the same HTML/TXT templates and context as the preview.
    """
    week = request.args.get("week", type=int) or current_week_number()

    # Collect recipients
    subs = (
        User.query
        .filter(User.notify_weekly_recap.is_(True))
        .filter(User.email.isnot(None))
        .all()
    )
    total = len(subs)
    if not total:
        flash("No subscribers with notify_weekly_recap enabled.", "warning")
        return redirect(url_for("admin.actions", week=week))

    # Build the email once; reuse bodies per recipient
    ctx = build_weekly_spreads_context(week, locked=True)
    subject = f"Week {ctx['week_number'] - 1} Results / Week {ctx['week_number']} Spreads"
    html_body = render_template("email/weekly_spreads.html", **ctx)
    try:
        text_body = render_template("email/weekly_spreads.txt", **ctx)
    except Exception:
        text_body = None

    sent = 0
    failed = []

    # NOTE: Free SendGrid = 100/day. We send one email per recipient.
    for u in subs:
        ok = send_email(subject=subject, recipients=u.email, html=html_body, text=text_body)
        if ok:
            sent += 1
        else:
            failed.append(u.email)

        # tiny pause to be gentle with rate limits (adjust if needed)
        sleep(0.2)

    msg = f"Weekly email (Week {week}) — attempted {total}, sent {sent}, failed {len(failed)}."
    if failed:
        current_app.logger.warning("Weekly bulk send failed for: %s", failed)
        flash(msg, "warning")
    else:
        flash(msg, "success")

    return redirect(url_for("admin.actions", week=week))

def _send_weekly_to_subscribers(week: int, log_row: WeeklyEmailLog) -> tuple[int, int, list[str]]:
    # Build the email bodies once
    ctx = build_weekly_spreads_context(week, locked=True)
    subject = f"Week {ctx['week_number'] - 1} Results / Week {ctx['week_number']} Spreads"
    html_body = render_template("email/weekly_spreads.html", **ctx)
    try:
        text_body = render_template("email/weekly_spreads.txt", **ctx)
    except Exception:
        text_body = None

    # Recipients
    subs = (
        User.query
        .filter(User.notify_weekly_recap.is_(True))
        .filter(User.email.isnot(None))
        .all()
    )

    total = len(subs)
    # ✅ explicit attribute assignment (no kwargs to model __init__)
    log_row.subject = subject
    log_row.total = total
    db.session.add(log_row)
    db.session.commit()

    sent = 0
    failed_emails: List[str] = []

    for i, u in enumerate(subs, start=1):
        email = u.email
        ok = False
        err: str | None = None

        try:
            ok = bool(send_email(subject=subject, recipients=email, html=html_body, text=text_body))
        except Exception as e:
            ok = False
            err = repr(e)

        rec = WeeklyEmailRecipientLog()   # ✅ no kwargs
        rec.log_id = log_row.id
        rec.email = email
        rec.status = "sent" if ok else "failed"
        rec.error = err
        db.session.add(rec)

        if ok:
            sent += 1
        else:
            failed_emails.append(email)

        # keep transactions reasonable
        if i % 50 == 0:
            db.session.commit()

    # finalize
    log_row.sent = sent
    log_row.failed = len(failed_emails)
    log_row.status = "sent"
    db.session.add(log_row)
    db.session.commit()

    return sent, total, failed_emails

@bp.post("/internal/cron/weekly-email")
def cron_weekly_email():
    token = request.args.get("token") or request.headers.get("X-CRON-TOKEN")
    if not token or token != current_app.config.get("CRON_SECRET"):
        abort(401)

    week   = request.args.get("week", type=int) or current_week_number()
    force  = str(request.args.get("force", "")).strip().lower() in ("1", "true", "yes", "y", "on")
    resend = str(request.args.get("resend","")).strip().lower() in ("1", "true", "yes", "y", "on")

    now_local = datetime.now(ZoneInfo("America/Denver"))
    if not force and (now_local.weekday() != 1 or now_local.hour != 12):
        return jsonify({"ok": True, "skipped": True, "reason": "not local Tue 12:00"}), 200

    subject = f"Week {week} NFL Spreads"
    season_id = current_season_id()

    # Acquire lock row via unique(season_id, week, kind)
    try:
        lock = WeeklyEmailLog()          # ✅ no kwargs
        lock.season_id = season_id
        lock.week = week
        lock.subject = subject
        lock.status = "started"
        db.session.add(lock)
        db.session.commit()
        acquired = True
    except IntegrityError:
        db.session.rollback()
        existing = WeeklyEmailLog.query.filter_by(week=week, season_id=season_id).first()
        if existing is None:
            # extremely rare: race; create a fresh row
            lock = WeeklyEmailLog()
            lock.season_id = season_id
            lock.week = week
            lock.subject = subject
            lock.status = "started"
            db.session.add(lock)
            db.session.commit()
            acquired = True
        elif not resend:
            return jsonify({"ok": True, "skipped": True, "reason": "already sent (log exists)"}), 200
        else:
            # Resend: wipe recipient rows and reset counters
            WeeklyEmailRecipientLog.query.filter_by(log_id=existing.id).delete(synchronize_session=False)
            existing.total = 0
            existing.sent = 0
            existing.failed = 0
            existing.status = "started"
            db.session.add(existing)
            db.session.commit()
            lock = existing
            acquired = False

    try:
        sent, total, failed_list = _send_weekly_to_subscribers(week, log_row=lock)
    except Exception:
        lock.status = "failed"
        db.session.add(lock)
        db.session.commit()
        current_app.logger.exception("[cron_weekly_email] send failed week=%s", week)
        return jsonify({"ok": False, "error": "send_failed"}), 500

    return jsonify({
        "ok": True,
        "week": week,
        "acquired_lock": acquired,
        "total": total,
        "sent": sent,
        "failed": len(failed_list),
    }), 200

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

    season_id = current_season_id()

    # ---------- weekly picks for prev_week ----------
    pairs_week = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Game.week == prev_week, Game.season_id == season_id)
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
        .filter(Game.week <= prev_week, Game.season_id == season_id)
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

def _build_reminder_ctx(week: int):
    # You can reuse whatever you used for date range on weekly emails
    # Here we keep it minimal
    return {
        "week_number": week,
        "week_date_range_text": "",  # fill if you have a helper
        "weekly_lines_url": url_for("weekly_lines.weekly_lines", week=week, _external=True),
        "timezone_name": "MT",
    }

def _users_missing_picks(week: int, picks_per_week: int):
    q = (User.query
         .filter(User.email.isnot(None))
         .filter(User.notify_picks_reminder.is_(True)))
    result = []
    for u in q.all():
        remaining, wk = remaining_picks_this_week(u.id, picks_per_week)
        if wk == week and remaining and remaining > 0:
            result.append((u, remaining))
    return result

@bp.get("/email/picks-reminder/preview")
@login_required
def preview_picks_reminder():
    week = request.args.get("week", type=int) or current_week_number()
    ctx = _build_reminder_ctx(week)
    return render_template("email/picks_reminder.html", **ctx)

@bp.get("/email/picks-reminder/preview.txt")
@login_required
def preview_picks_reminder_txt():
    week = request.args.get("week", type=int) or current_week_number()
    ctx = _build_reminder_ctx(week)
    return render_template("email/picks_reminder.txt", **ctx), 200, {"Content-Type": "text/plain; charset=utf-8"}

@bp.get("/email/picks-reminder/send-test")
@login_required
def send_picks_reminder_test():
    """Sends the picks-reminder template to a single address - for checking
    that SendGrid is actually working, not the real reminder run."""
    to = request.args.get("to")
    if not to:
        return "Add ?to=you@example.com", 400

    week = request.args.get("week", type=int) or current_week_number()
    ctx = _build_reminder_ctx(week)
    html_body = render_template("email/picks_reminder.html", **ctx)
    try:
        text_body = render_template("email/picks_reminder.txt", **ctx)
    except Exception:
        text_body = None

    try:
        send_email(
            subject=f"[TEST] Reminder: finish your Week {week} picks",
            recipients=to,
            html=html_body,
            text=text_body,
        )
        return "✅ sent", 200
    except Exception as e:
        return f"❌ failed: {e}", 500

def _send_picks_reminder_to_incomplete(week: int, log_row: WeeklyEmailLog) -> tuple[int, int, list[str]]:
    ctx = _build_reminder_ctx(week)
    subject = f"Reminder: finish your Week {week} picks"
    html_body = render_template("email/picks_reminder.html", **ctx)
    text_body = render_template("email/picks_reminder.txt", **ctx)

    picks_per_week = int(current_app.config.get("PICKS_PER_WEEK", 5))
    subs = _users_missing_picks(week, picks_per_week)

    total = len(subs)
    log_row.subject = subject
    log_row.total = total
    current_app.logger.info("[picks-reminder] week=%s recipients=%s", week, total)
    db.session.add(log_row); db.session.commit()

    sent = 0
    failed: list[str] = []

    for i, (u, remaining) in enumerate(subs, start=1):
        email = u.email
        ok = False
        err = None
        try:
            ok = bool(send_email(subject=subject, recipients=email, html=html_body, text=text_body))
        except Exception as e:
            ok = False
            err = repr(e)

        rec = WeeklyEmailRecipientLog()
        rec.log_id = log_row.id
        rec.email = email
        rec.status = "sent" if ok else "failed"
        rec.error = err
        db.session.add(rec)

        if ok:
            sent += 1
        else:
            failed.append(email)

        if i % 50 == 0:
            db.session.commit()

    log_row.sent = sent
    log_row.failed = len(failed)
    log_row.status = "sent"
    db.session.add(log_row); db.session.commit()
    return sent, total, failed

@bp.route("/email/picks-reminder/send", methods=["GET", "POST"])
@login_required
def send_picks_reminder_manual():
    week = request.args.get("week", type=int) or request.form.get("week", type=int) or current_week_number()
    resend = str(request.args.get("resend") or request.form.get("resend") or "").strip().lower() in ("1","true","yes","y","on")
    season_id = current_season_id()

    # Acquire a (season_id, week, kind='reminder') lock in weekly_email_log
    try:
        lock = WeeklyEmailLog()
        lock.season_id = season_id
        lock.week = week
        lock.kind = "reminder"
        lock.subject = f"Reminder: finish your Week {week} picks"
        lock.status = "started"
        db.session.add(lock)
        db.session.commit()
        acquired = True
    except IntegrityError:
        # A log row already exists for (season_id, week, reminder)
        db.session.rollback()
        existing = WeeklyEmailLog.query.filter_by(week=week, kind="reminder", season_id=season_id).first()

        if not existing:
            flash("Existing reminder log not found; please try again.", "error")
            return redirect(url_for("admin.actions", week=week))

        if not resend:
            flash(f"Reminder for week {week} already sent or queued. Use Re-send to force it.", "warning")
            return redirect(url_for("admin.actions", week=week))

        # Re-send: clear recipient rows and reset counters
        WeeklyEmailRecipientLog.query.filter_by(log_id=existing.id).delete(synchronize_session=False)
        existing.total = 0
        existing.sent = 0
        existing.failed = 0
        existing.status = "started"
        db.session.add(existing)
        db.session.commit()
        lock = existing
        acquired = False

    # Do the send
    try:
        sent, total, failed = _send_picks_reminder_to_incomplete(week, lock)
        flash(f"Reminder (Week {week}) — attempted {total}, sent {sent}, failed {len(failed)}.", "success" if not failed else "warning")
    except Exception:
        lock.status = "failed"
        db.session.add(lock); db.session.commit()
        current_app.logger.exception("[picks-reminder manual] failed week=%s", week)
        flash("Reminder send failed. See logs.", "error")

    return redirect(url_for("admin.actions", week=week))


@bp.post("/internal/cron/picks-reminder")
def cron_picks_reminder():
    token = request.headers.get("X-CRON-TOKEN") or request.args.get("token")
    if not token or token != current_app.config.get("CRON_SECRET"):
        abort(401)

    week  = request.args.get("week", type=int) or current_week_number()
    force = str(request.args.get("force","")).lower() in ("1","true","yes","y","on")

    now_local = datetime.now(ZoneInfo("America/Denver"))
    if not force and not (now_local.weekday() == 6 and now_local.hour == 10):  # Sunday=6
        return jsonify({"ok": True, "skipped": True, "reason": "not local Sun 10:00"}), 200

    try:
        lock = WeeklyEmailLog()
        lock.season_id = current_season_id()
        lock.week = week
        lock.kind = "reminder"
        lock.subject = f"Reminder: finish your Week {week} picks"
        lock.status = "started"
        db.session.add(lock); db.session.commit()
        acquired = True
    except IntegrityError:
        db.session.rollback()
        return jsonify({"ok": True, "skipped": True, "reason": "already sent (season,week,kind)"}), 200

    try:
        sent, total, failed = _send_picks_reminder_to_incomplete(week, lock)
        return jsonify({"ok": True, "week": week, "total": total, "sent": sent, "failed": len(failed)}), 200
    except Exception:
        lock.status = "failed"; db.session.add(lock); db.session.commit()
        current_app.logger.exception("[cron picks-reminder] failed week=%s", week)
        return jsonify({"ok": False, "error": "send_failed"}), 500
