from . import bp
from flask import render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from collections import OrderedDict
from datetime import datetime, timezone
from sqlalchemy import func
from app.extensions import db
from app.models import Game, Pick, TeamGameATS
from app.services.time_utils import day_key, time_key
from app.filters import abbr_team


# ============================================================================
# PAGE: Weekly lines (public; only locked/published weeks/games)
# GET /lines
# ============================================================================
@bp.get("/lines")
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

    # Nothing published yet -> empty polite page
    if not visible_weeks:
        return render_template(
            "weekly_lines.html",
            groups=[],
            weeks=[],
            selected_week=None,
            tzname=tzname,
            picks_by_game={},
            now_utc=datetime.now(timezone.utc),
            abbr_team=abbr_team,
        )

    # Pick week (param or latest published)
    selected_week = request.args.get("week", type=int)
    if selected_week not in visible_weeks:
        selected_week = max(visible_weeks)

    # Only locked games for the selected week
    games = (
        Game.query
        .filter(Game.week == selected_week, Game.spread_is_locked.is_(True))
        .order_by(Game.kickoff_at.asc(), Game.id.asc())
        .all()
    )

    # ATS map
    game_ids = [g.id for g in games]
    ats_rows = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all()
    ats_by_game = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows}

    # Group by day/time
    days: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in games:
        day_title, day_sort = day_key(g.kickoff_at, tzname)
        if (day_title, day_sort) not in days:
            days[(day_title, day_sort)] = OrderedDict()
        times = days[(day_title, day_sort)]
        time_title, time_sort = time_key(g.kickoff_at, tzname)
        if (time_title, time_sort) not in times:
            times[(time_title, time_sort)] = []
        times[(time_title, time_sort)].append(g)

    now_utc = datetime.now(timezone.utc)

    # Current user's picks (for pre-check in the list)
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

    return render_template(
        "weekly_lines.html",
        groups=groups,
        weeks=visible_weeks,
        selected_week=selected_week,
        tzname=tzname,
        picks_by_game=picks_by_game,
        now_utc=now_utc,
        ats_by_game=ats_by_game,
        abbr_team=abbr_team,
    )


# ============================================================================
# FRAGMENT: list only (for AJAX swapping without reload)
# GET /lines/fragment
# ============================================================================
@bp.get("/lines/fragment")
def weekly_lines_fragment():
    selected_week = request.args.get("week", type=int)
    tzname = request.args.get("tz")

    if selected_week is None:
        row = (
            db.session.query(Game.week)
            .filter(Game.spread_is_locked.is_(True))
            .order_by(Game.week.desc())
            .first()
        )
        if row:
            selected_week = row[0]
        else:
            # nothing to show
            return render_template(
                "partials/_weekly_lines_list.html",
                groups=[],
                picks_by_game={},
                selected_week=None,
                now_utc=datetime.now(timezone.utc),
                tzname=tzname,
                ats_by_game={},
                abbr_team=abbr_team,
            )

    # Locked games for week
    games = (
        Game.query
        .filter(Game.week == selected_week, Game.spread_is_locked.is_(True))
        .order_by(Game.kickoff_at.asc(), Game.id.asc())
        .all()
    )

    game_ids = [g.id for g in games]
    ats_rows = (TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all()) if game_ids else []
    ats_by_game = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows}

    # Group by day/time
    days: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in games:
        day_title, day_sort = day_key(g.kickoff_at, tzname)
        times = days.setdefault((day_title, day_sort), OrderedDict())
        time_title, time_sort = time_key(g.kickoff_at, tzname)
        times.setdefault((time_title, time_sort), []).append(g)

    groups = []
    for (day_title, _), times in days.items():
        groups.append((day_title, [(t[0], items) for t, items in times.items()]))

    now_utc = datetime.now(timezone.utc)

    # Current user's picks (for pre-check)
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

    return render_template(
        "partials/_weekly_lines_list.html",
        groups=groups,
        picks_by_game=picks_by_game,
        now_utc=now_utc,
        selected_week=selected_week,
        tzname=tzname,
        ats_by_game=ats_by_game,
        abbr_team=abbr_team,
    )


# ============================================================================
# JSON API: save picks without full reload
# POST /api/picks
# ============================================================================
@bp.post("/api/picks")
@login_required
def submit_picks_api():
    """
    Body: { "week": <int>, "picks": ["<game_id>::<team_name>", ...] }
    Returns: { ok, saved, committed_picks:[{game_id, team, abbr}] }
    """
    data = request.get_json(silent=True) or {}
    week = int(data.get("week", 0))
    raw = data.get("picks", []) or []

    if not week:
        return jsonify(ok=False, error="Missing week"), 400
    if not raw:
        return jsonify(ok=False, error="No picks provided"), 400
    if len(raw) > 5:
        return jsonify(ok=False, error="Max 5 picks"), 400

    # Parse
    parsed = []
    for item in raw:
        try:
            gid_str, team = item.split("::", 1)
            parsed.append((int(gid_str), team))
        except Exception:
            return jsonify(ok=False, error=f"Bad pick value: {item!r}"), 400

    # Validate games belong to this week
    game_ids = [gid for gid, _ in parsed]
    games = Game.query.filter(Game.id.in_(game_ids), Game.week == week).all()
    lookup = {g.id: g for g in games}
    if len(lookup) != len(game_ids):
        return jsonify(ok=False, error="One or more picks not found for this week"), 400

    # Enforce kickoff locks & 5-pick cap (counting already-locked picks)
    now_utc = datetime.now(timezone.utc)
    locked_existing = (
        db.session.query(Pick)
        .join(Game, Game.id == Pick.game_id)
        .filter(
            Pick.user_id == current_user.id,
            Game.week == week,
            Game.kickoff_at <= now_utc,  # already locked picks
        )
        .count()
    )
    remaining_slots = max(0, 5 - locked_existing)

    # Only consider selections for games that haven't kicked off
    filtered = []
    for gid, team in parsed:
        g = lookup.get(gid)
        if not g or team not in (g.home_team, g.away_team):
            continue
        if not g.kickoff_at or g.kickoff_at <= now_utc:
            continue  # locked
        filtered.append((gid, team))

    # Trim to remaining slots
    filtered = filtered[:remaining_slots]

    # Clear existing UNLOCKED picks this week (no join in delete)
    subq_ids = (
        db.session.query(Game.id)
        .filter(Game.week == week, Game.kickoff_at > now_utc)
        .subquery()
    )
    (
        db.session.query(Pick)
        .filter(Pick.user_id == current_user.id, Pick.game_id.in_(subq_ids))
        .delete(synchronize_session=False)
    )

    # Insert new picks
    for gid, team in filtered:
        p = Pick()
        p.user_id = current_user.id
        p.game_id = gid
        p.chosen_team = team
        db.session.add(p)

    db.session.commit()

    # Return committed (DB truth) for the card
    committed = (
        db.session.query(Pick, Game)
        .join(Game, Pick.game_id == Game.id)
        .filter(Pick.user_id == current_user.id, Game.week == week)
        .all()
    )
    committed_picks = [
        {"game_id": g.id, "team": p.chosen_team, "abbr": abbr_team(p.chosen_team)}
        for (p, g) in committed
    ]

    return jsonify(ok=True, saved=len(committed_picks), committed_picks=committed_picks)


# ============================================================================
# Non-JS fallback submit (kept for robustness)
# POST /lines/submit
# ============================================================================
@bp.post("/lines/submit")
@login_required
def submit_picks():
    week = request.form.get("week", type=int)
    tzname = request.form.get("tz")  # keep context for redirect
    if week is None:
        flash("Missing week.", "error")
        return redirect(url_for("weekly_lines.weekly_lines"))

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
        selections = selections[:5]

    now_utc = datetime.now(timezone.utc)

    # Delete existing UNLOCKED picks for this week
    subq_game_ids = (
        db.session.query(Game.id)
        .filter(Game.week == week, Game.kickoff_at > now_utc)
        .subquery()
    )
    (
        db.session.query(Pick)
        .filter(Pick.user_id == current_user.id, Pick.game_id.in_(subq_game_ids))
        .delete(synchronize_session=False)
    )

    # Count locked picks; compute remaining slots
    locked_existing = (
        db.session.query(Pick)
        .join(Game, Game.id == Pick.game_id)
        .filter(
            Pick.user_id == current_user.id,
            Game.week == week,
            Game.kickoff_at <= now_utc,
        )
        .count()
    )
    remaining_slots = max(0, 5 - locked_existing)

    # Insert new picks for games that haven't kicked off
    inserted = 0
    for gid, team in selections[:remaining_slots]:
        g = Game.query.get(gid)
        if not g or g.week != week or team not in (g.home_team, g.away_team) or not g.kickoff_at or g.kickoff_at <= now_utc:
            continue
        p = Pick()
        p.user_id = current_user.id
        p.game_id = gid
        p.chosen_team = team
        db.session.add(p)
        inserted += 1

    db.session.commit()
    flash(f"Saved {inserted} pick(s).", "success")
    return redirect(url_for("weekly_lines.weekly_lines", week=week, tz=tzname))
