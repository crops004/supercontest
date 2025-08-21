from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app.scoring import points_for_pick, game_result_against_spread
from typing import Dict, List, cast
from app.types import AggRow
from datetime import datetime, timezone, timedelta
from collections import OrderedDict
import os

from app.extensions import db
from app.models import Game, Pick, User, TeamGameATS

bp = Blueprint('main', __name__)


# --- HELPERS ---
# --- admin access control ---
def admin_required():
    if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
        abort(403)

# --- small helpers for default week (Thu-anchored) ---
def _parse_iso_z(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def _thursday_anchor_utc() -> datetime:
    # e.g. NFL_WEEK1_THURSDAY_UTC=2025-09-04T00:00:00Z
    anchor = os.getenv("NFL_WEEK1_THURSDAY_UTC")
    if not anchor:
        # fall back to current week's Thu 00:00 UTC
        now = datetime.now(timezone.utc)
        days_back = (now.weekday() - 3) % 7  # Thu=3
        return now - timedelta(days=days_back,
                               hours=now.hour, minutes=now.minute,
                               seconds=now.second, microseconds=now.microsecond)
    return _parse_iso_z(anchor).astimezone(timezone.utc)

def _week_from_thursday(dt: datetime, anchor: datetime) -> int:
    dt = dt.astimezone(timezone.utc)
    if dt < anchor:
        return 0
    return ((dt - anchor).days // 7) + 1

def _current_week_number() -> int:
    return _week_from_thursday(datetime.now(timezone.utc), _thursday_anchor_utc())

# --- display timezone helpers ---
try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:
    ZoneInfo = None

def _to_display_tz(dt: datetime, tzname: str | None) -> datetime:
    """Convert a datetime to the requested tz (IANA name), else leave tz / assume UTC."""
    if tzname and ZoneInfo:
        try:
            return dt.astimezone(ZoneInfo(tzname))
        except Exception:
            pass
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _fmt_time_short(d: datetime) -> str:
    # Cross-platform (no %-I): strip leading zero from %I manually.
    h = d.strftime('%I').lstrip('0') or '0'
    m = d.strftime('%M')
    ap = d.strftime('%p')
    tzabbr = d.strftime('%Z')
    return f"{h}:{m} {ap} {tzabbr}"  # e.g., 6:15 PM MST

def _day_key(dt: datetime | None, tzname: str | None):
    if dt is None:
        return ("TBD", None)
    d = _to_display_tz(dt, tzname)
    return (d.strftime("%A"), d.date())  # title, sort key

def _time_key(dt: datetime | None, tzname: str | None):
    if dt is None:
        return ("TBD", None)
    d = _to_display_tz(dt, tzname)
    return (_fmt_time_short(d), d.strftime("%H:%M"))  # title, chrono sort key

# --- helpers for weekly-lines visibility ---
def visible_weeks():
    """Weeks that are published = any game in that week has spread_is_locked = true."""
    rows = (
        db.session.query(Game.week)
        .filter(Game.spread_is_locked.is_(True))
        .distinct()
        .order_by(Game.week.asc())
        .all()
    )
    return [w for (w,) in rows]

# --- Jinja format helpers ---
@bp.app_template_filter("fmt_spread")
def fmt_spread(value):
    if value is None:
        return ""
    v = float(value)
    return str(int(v)) if v.is_integer() else f"{v:.1f}"

@bp.app_template_global()
def is_pickem(game: Game) -> bool:
    """True when both sides are 0 (or effectively 0)."""
    sh, sa = game.spread_home, game.spread_away
    if sh is None or sa is None:
        return False
    return abs(float(sh)) < 1e-9 and abs(float(sa)) < 1e-9

# --- ROUTES ---
@bp.route('/')
def home():
    return render_template('home.html')

# --- Weekly lines page (public; show only locked/published weeks/games) ---
@bp.route("/lines")
def weekly_lines():
    tzname = request.args.get("tz")  # e.g., America/Denver

    # Weeks that are published (at least one game locked)
    visible_weeks = [
        w for (w,) in (
            db.session.query(Game.week)
            .filter(Game.spread_is_locked.is_(True))
            .distinct()
            .order_by(Game.week.asc())
            .all()
        ) if w is not None
    ]

    # If nothing is published yet, render an empty page politely
    if not visible_weeks:
        return render_template(
            "weekly_lines.html",
            groups=[], weeks=[], selected_week=None, tzname=tzname,
            picks_by_game={}, now_utc=datetime.now(timezone.utc),
        )

    # Pick week: use request param if valid; otherwise default to latest published
    selected_week = request.args.get("week", type=int)
    if selected_week not in visible_weeks:
        selected_week = max(visible_weeks)

    # Only locked games for the selected (published) week
    games = (
        Game.query
            .filter(Game.week == selected_week, Game.spread_is_locked.is_(True))
            .order_by(Game.kickoff_at.asc(), Game.id.asc())
            .all()
    )

    game_ids = [g.id for g in games]
    ats_rows = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all()
    # map (game_id, team_name) -> 'COVER' | 'PUSH' | 'NO_COVER' | None
    ats_by_game = { (r.game_id, r.team): (r.ats_result or None) for r in ats_rows }

    # ---- your existing grouping logic (unchanged) ----
    days: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in games:
        day_title, day_sort = _day_key(g.kickoff_at, tzname)
        if (day_title, day_sort) not in days:
            days[(day_title, day_sort)] = OrderedDict()
        times = days[(day_title, day_sort)]
        time_title, time_sort = _time_key(g.kickoff_at, tzname)
        if (time_title, time_sort) not in times:
            times[(time_title, time_sort)] = []
        times[(time_title, time_sort)].append(g)

    now_utc = datetime.now(timezone.utc)

    picks_by_game = {}
    if current_user.is_authenticated:
        picked = (
            db.session.query(Pick)
            .join(Game, Game.id == Pick.game_id)
            .filter(Pick.user_id == current_user.id, Game.week == selected_week)
            .all()
        )
        for p in picked:
            picks_by_game[p.game_id] = p.chosen_team

    groups = []
    for (day_title, _), times in days.items():
        time_list = [(t[0], items) for t, items in times.items()]
        groups.append((day_title, time_list))

    # Pass only published weeks to the dropdown
    return render_template(
        "weekly_lines.html",
        groups=groups,
        weeks=visible_weeks,
        selected_week=selected_week,
        tzname=tzname,
        picks_by_game=picks_by_game,
        now_utc=now_utc,
        ats_by_game=ats_by_game,
    )

# Returns only the list markup so the page can swap it without reload
@bp.route("/lines/fragment")
def weekly_lines_fragment():
    selected_week = request.args.get("week", type=int)
    if selected_week is None:
        # fall back to latest locked week
        row = (db.session.query(Game.week)
               .filter(Game.spread_is_locked.is_(True))
               .order_by(Game.week.desc())
               .first())
        if row:
            selected_week = row[0]
        else:
            # nothing to show
            return render_template(
                "partials/_weekly_lines_list.html",
                groups=[],
                picks_by_game={},
                now_utc=datetime.now(timezone.utc),
            )

    tzname = request.args.get("tz")

    # Only show locked games in the requested week
    games = (Game.query
                  .filter(Game.week == selected_week,
                          Game.spread_is_locked.is_(True))
                  .order_by(Game.kickoff_at.asc(), Game.id.asc())
                  .all())

    # Group by day/time (same as before)
    days: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in games:
        day_title, day_sort = _day_key(g.kickoff_at, tzname)
        times = days.setdefault((day_title, day_sort), OrderedDict())
        time_title, time_sort = _time_key(g.kickoff_at, tzname)
        times.setdefault((time_title, time_sort), []).append(g)

    groups = []
    for (day_title, _), times in days.items():
        groups.append((day_title, [(t[0], items) for t, items in times.items()]))

    now_utc = datetime.now(timezone.utc)

    # Current user's picks for this (locked) week, to pre-check and disable after kickoff
    picks_by_game = {}
    if current_user.is_authenticated:
        picked = (db.session.query(Pick)
                  .join(Game, Game.id == Pick.game_id)
                  .filter(Pick.user_id == current_user.id,
                          Game.week == selected_week)
                  .all())
        for p in picked:
            picks_by_game[p.game_id] = p.chosen_team

    return render_template(
        "partials/_weekly_lines_list.html",
        groups=groups,
        picks_by_game=picks_by_game,
        now_utc=now_utc,
        selected_week=selected_week,
        tzname=tzname,
    )

@bp.route("/lines/submit", methods=["POST"])
@login_required
def submit_picks():
    week = request.form.get("week", type=int)
    tzname = request.form.get("tz")  # keep context for redirect
    if week is None:
        flash("Missing week.", "error")
        return redirect(url_for("main.weekly_lines"))

    raw = request.form.getlist("picks")  # ["<game_id>|<team>", ...]
    # Parse & validate
    selections = []
    seen_games = set()
    for item in raw:
        try:
            gid_str, team = item.split("|", 1)
            gid = int(gid_str)
        except Exception:
            continue
        if gid in seen_games:
            continue  # at most one team per game
        selections.append((gid, team))
        seen_games.add(gid)

    if len(selections) > 5:
        flash("You can select at most 5 picks.", "error")
        # Trim to 5 to avoid hard failure; server still enforces.
        selections = selections[:5]

    now_utc = datetime.now(timezone.utc)

    # --- Delete your existing UNLOCKED picks for this week (no JOIN in the delete) ---
    now_utc = datetime.now(timezone.utc)

    subq_game_ids = (
        db.session.query(Game.id)
        .filter(Game.week == week, Game.kickoff_at > now_utc)  # only games not kicked off
        .subquery()
    )

    db.session.query(Pick).filter(
        Pick.user_id == current_user.id,
        Pick.game_id.in_(subq_game_ids)
    ).delete(synchronize_session=False)

    # --- Insert new picks (only for games in this week & not kicked off) ---
    # If you want the 5-pick cap to include already-locked picks from earlier in the week:
    locked_existing = (
        db.session.query(Pick)
        .join(Game, Game.id == Pick.game_id)
        .filter(Pick.user_id == current_user.id,
                Game.week == week,
                Game.kickoff_at <= now_utc)
        .count()
    )
    remaining_slots = max(0, 5 - locked_existing)
    if len(selections) > remaining_slots:
        selections = selections[:remaining_slots]

    inserted = 0
    for gid, team in selections:
        g = Game.query.get(gid)
        if not g or g.week != week:
            continue
        if team not in (g.home_team, g.away_team):
            continue
        if not g.kickoff_at or g.kickoff_at <= now_utc:
            continue  # locked

        p = Pick()
        p.user_id = current_user.id
        p.game_id = gid
        p.chosen_team = team
        db.session.add(p)
        inserted += 1

    db.session.commit()
    flash(f"Saved {inserted} pick(s).", "success")
    return redirect(url_for("main.weekly_lines", week=week, tz=tzname))