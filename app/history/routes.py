from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models import Game, Season
from app.services.standings_trend import (
    REGULAR_SEASON_WEEKS,
    get_cumulative_points_trend,
    get_final_standings,
)

from . import bp


@bp.get("/")
@login_required
def history():
    seasons = Season.query.filter_by(is_active=False).order_by(Season.year.asc()).all()

    if not seasons:
        return render_template("history.html", has_seasons=False)

    years = [s.year for s in seasons]
    selected_year = request.args.get("year", type=int)
    if selected_year not in years:
        selected_year = years[-1]  # most recent completed season by default

    season = next(s for s in seasons if s.year == selected_year)
    idx = years.index(selected_year)

    actual_max_week = (
        db.session.query(func.max(Game.week))
        .filter(Game.season_id == season.id)
        .scalar()
    ) or 1
    # Week 19 (tiebreaker) only counts if explicitly enabled for this season.
    max_week = actual_max_week if season.uses_week19 else min(actual_max_week, REGULAR_SEASON_WEEKS)

    rows = get_final_standings(season.id, max_week=max_week)
    trend = get_cumulative_points_trend(season.id, max_week)

    winner = rows[0] if rows else None
    last_place = rows[-1] if len(rows) > 1 else None

    return render_template(
        "history.html",
        has_seasons=True,
        years=years,
        selected_year=selected_year,
        show_left=idx > 0,
        show_right=idx < len(years) - 1,
        prev_year=years[idx - 1] if idx > 0 else None,
        next_year=years[idx + 1] if idx < len(years) - 1 else None,
        rows=rows,
        trend=trend,
        winner=winner,
        last_place=last_place,
    )
