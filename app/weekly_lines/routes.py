from . import bp
from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from collections import OrderedDict
from datetime import datetime, timezone
from sqlalchemy import func
from app.extensions import db
from app.models import Game, Pick, TeamGameATS
from app.services.time_utils import day_key, time_key
from app.filters import abbr_team


def _canon(s: str) -> str:
    """Lowercase, trimmed string for robust keying."""
    return (s or "").strip().lower()

def _norm_ats(x: str):
    x = (x or "").strip().upper()
    return {
        "W": "W", "WIN": "W", "COVER": "W",
        "L": "L", "LOSS": "L", "LOSE": "L", "NO_COVER": "L",
        "P": "P", "PUSH": "P",
    }.get(x, None)

def _build_groups_by_day_time(games, tzname):
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

    groups = []
    for (day_title, _), times in days.items():
        time_list = [(t[0], items) for t, items in times.items()]
        groups.append((day_title, time_list))
    return groups


def _resolve_ats_for_games(games, debug=False):
    """
    Return:
      ats_resolved: dict[(game_id, 'home'|'away')] -> 'W'|'L'|'P'|None
      ats_debug: list[dict] if debug else []
    """
    ats_resolved = {}
    dbg = []
    if not games:
        return ats_resolved, dbg

    game_ids = [g.id for g in games]
    rows = TeamGameATS.query.filter(TeamGameATS.game_id.in_(game_ids)).all()

    ats_idx = { (r.game_id, _canon(r.team)): _norm_ats(r.ats_result) for r in rows }

    for g in games:
        home_keys = [_canon(g.home_team), _canon(abbr_team(g.home_team))]
        away_keys = [_canon(g.away_team), _canon(abbr_team(g.away_team))]

        res_home = next((ats_idx.get((g.id, k)) for k in home_keys if ats_idx.get((g.id, k)) is not None), None)
        res_away = next((ats_idx.get((g.id, k)) for k in away_keys if ats_idx.get((g.id, k)) is not None), None)

        # (optional) simple fallback if the game says completed and fields exist
        if (res_home is None or res_away is None) and getattr(g, "completed", False):
            try:
                if g.spread_home is not None and g.final_score_home is not None and g.final_score_away is not None:
                    margin = (g.final_score_home + g.spread_home) - g.final_score_away
                    if res_home is None:
                        res_home = 'W' if margin > 0 else 'L' if margin < 0 else 'P'
                    if res_away is None:
                        res_away = 'L' if margin > 0 else 'W' if margin < 0 else 'P'
            except Exception as e:
                current_app.logger.exception(f"ATS fallback error for game {g.id}: {e}")

        ats_resolved[(g.id, 'home')] = res_home
        ats_resolved[(g.id, 'away')] = res_away

        if debug:
            # collect source rows for this game
            src = [dict(game_id=r.game_id, team=r.team, ats_result=r.ats_result)
                   for r in rows if r.game_id == g.id]
            dbg.append({
                "game_id": g.id,
                "home_team": g.home_team,
                "away_team": g.away_team,
                "home_keys": home_keys,
                "away_keys": away_keys,
                "source_rows": src,
                "resolved_home": res_home,
                "resolved_away": res_away,
                "completed": bool(getattr(g, "completed", False)),
                "final_score_home": getattr(g, "final_score_home", None),
                "final_score_away": getattr(g, "final_score_away", None),
                "spread_home": getattr(g, "spread_home", None),
                "spread_away": getattr(g, "spread_away", None),
            })

    if debug:
        current_app.logger.info("ATS DEBUG SNAPSHOT: %s", dbg)

    return ats_resolved, dbg


# ============================================================================
# PAGE: Weekly lines (public; only locked/published weeks/games)
# GET /lines
# ============================================================================
@bp.get("/lines")
def weekly_lines():
    tzname = request.args.get("tz")  # e.g., America/Denver
    debug  = request.args.get("debug") == "1"

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
            ats_resolved={},   # new
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

    # Resolve ATS per side (robust)
    ats_resolved, ats_debug = _resolve_ats_for_games(games, debug=debug)

    # Group by day/time
    groups = _build_groups_by_day_time(games, tzname)

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

    return render_template(
        "weekly_lines.html",
        groups=groups,
        weeks=visible_weeks,
        selected_week=selected_week,
        tzname=tzname,
        picks_by_game=picks_by_game,
        now_utc=now_utc,
        abbr_team=abbr_team,
        ats_resolved=ats_resolved,
        ats_debug=ats_debug if debug else [],
        debug=debug,   # new
    )


# ============================================================================
# FRAGMENT: list only (for AJAX swapping without reload)
# GET /lines/fragment
# ============================================================================
@bp.get("/lines/fragment")
def weekly_lines_fragment():
    selected_week = request.args.get("week", type=int)
    tzname = request.args.get("tz")
    debug  = request.args.get("debug") == "1"

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
                abbr_team=abbr_team,
                ats_resolved={},   # new
            )

    # Locked games for week
    games = (
        Game.query
        .filter(Game.week == selected_week, Game.spread_is_locked.is_(True))
        .order_by(Game.kickoff_at.asc(), Game.id.asc())
        .all()
    )

    # Resolve ATS per side (robust)
    ats_resolved, ats_debug = _resolve_ats_for_games(games, debug=debug)

    # Group by day/time
    groups = _build_groups_by_day_time(games, tzname)

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
        abbr_team=abbr_team,
        ats_resolved=ats_resolved,
        ats_debug=ats_debug if debug else [],
        debug=debug,   # new
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

    # --- accept week 0; validate presence & type ---
    if "week" not in data:
        return jsonify(ok=False, error="Missing week"), 400
    try:
        week = int(data["week"])
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Bad week"), 400

    raw = data.get("picks", []) or []
    if len(raw) > 5:
        return jsonify(ok=False, error="Max 5 picks"), 400

    # --- parse + de-dupe (one pick per game) ---
    parsed = []
    seen_games = set()
    for item in raw:
        try:
            gid_str, team = item.split("::", 1)
            gid = int(gid_str)
        except Exception:
            return jsonify(ok=False, error=f"Bad pick value: {item!r}"), 400
        if gid in seen_games:
            continue
        parsed.append((gid, team))
        seen_games.add(gid)

    now_utc = datetime.now(timezone.utc)

    # --- validate games belong to this week (only if we have any) ---
    if parsed:
        game_ids = [gid for gid, _ in parsed]
        games = Game.query.filter(Game.id.in_(game_ids), Game.week == week).all()
        lookup = {g.id: g for g in games}
        if len(lookup) != len(game_ids):
            return jsonify(ok=False, error="One or more picks not found for this week"), 400
    else:
        lookup = {}

    # --- count already locked picks to compute remaining slots (max 5) ---
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

    # --- keep only valid, not-started games; trim to remaining slots ---
    filtered = []
    for gid, team in parsed:
        g = lookup.get(gid)
        if not g or team not in (g.home_team, g.away_team):
            continue
        if not g.kickoff_at or g.kickoff_at <= now_utc:
            continue
        filtered.append((gid, team))
        if len(filtered) >= remaining_slots:
            break

    # --- delete existing UNLOCKED picks for this week (0-new-picks → clear) ---
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

    # --- insert new picks ---
    for gid, team in filtered:
        p = Pick()
        p.user_id = current_user.id
        p.game_id = gid
        p.chosen_team = team
        db.session.add(p)

    db.session.commit()

    # --- return DB truth (locked + newly saved) ---
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
    tzname = request.form.get("tz")
    if week is None:
        flash("Missing week.", "error")
        return redirect(url_for("weekly_lines.weekly_lines"))

    raw = request.form.getlist("picks")  # ["<game_id>|<team>", ...]
    selections = []
    seen_games = set()
    for item in raw:
        try:
            gid_str, team = item.split("|", 1)
            gid = int(gid_str)
        except Exception:
            continue
        if gid in seen_games:
            continue
        selections.append((gid, team))
        seen_games.add(gid)

    if len(selections) > 5:
        flash("You can select at most 5 picks.", "error")
        selections = selections[:5]

    now_utc = datetime.now(timezone.utc)

    # Delete existing UNLOCKED picks for this week (also handles empty → clear)
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
    for gid, team in selections:
        if inserted >= remaining_slots:
            break
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

