# app/admin/seasons.py
from __future__ import annotations

from datetime import date

from flask import request, render_template, redirect, url_for, flash
from flask_login import login_required

from app.extensions import db
from app.models import Season, Game, Pick, User, UserSeason
from app.services.standings_trend import REGULAR_SEASON_WEEKS
from app.services.roster import bootstrap_season_roster, get_or_create_user_season

from . import bp


@bp.get("/seasons")
@login_required
def seasons():
    rows = Season.query.order_by(Season.year.desc()).all()

    week19_pick_counts = {}
    for s in rows:
        count = (
            db.session.query(Pick.id)
            .join(Game, Pick.game_id == Game.id)
            .filter(Game.season_id == s.id, Game.week > REGULAR_SEASON_WEEKS)
            .count()
        )
        week19_pick_counts[s.id] = count

    return render_template("seasons.html", seasons=rows, week19_pick_counts=week19_pick_counts)


@bp.post("/seasons/<int:season_id>/toggle-week19")
@login_required
def toggle_season_week19(season_id):
    season = Season.query.get_or_404(season_id)
    season.uses_week19 = not season.uses_week19
    db.session.commit()
    state = "now counts" if season.uses_week19 else "no longer counts"
    flash(f"Week 19 {state} toward standings/history for {season.year}.", "success")
    return redirect(url_for("admin.seasons"))


@bp.post("/seasons")
@login_required
def create_season():
    year = request.form.get("year", type=int)
    name = (request.form.get("name") or "").strip() or None
    start_date_str = (request.form.get("start_date") or "").strip()

    if not year:
        flash("Year is required.", "error")
        return redirect(url_for("admin.seasons"))

    start_date = None
    if start_date_str:
        try:
            start_date = date.fromisoformat(start_date_str)
        except ValueError:
            flash("Start date must be YYYY-MM-DD.", "error")
            return redirect(url_for("admin.seasons"))

    if Season.query.filter_by(year=year).first():
        flash(f"A season for {year} already exists.", "error")
        return redirect(url_for("admin.seasons"))

    # Only one season is ever active at a time (also enforced by a DB constraint).
    Season.query.filter_by(is_active=True).update({"is_active": False})

    season = Season()
    season.year = year
    season.name = name or f"{year} NFL"
    season.start_date = start_date
    season.is_active = True
    db.session.add(season)
    db.session.commit()

    bootstrap_season_roster(season.id)

    flash(f"Season {year} created and set as active.", "success")
    return redirect(url_for("admin.seasons"))


@bp.post("/seasons/<int:season_id>/make-active")
@login_required
def make_season_active(season_id):
    season = Season.query.get_or_404(season_id)
    if season.start_date is None:
        flash(f"Season {season.year} has no start date set — set one before making it active.", "error")
        return redirect(url_for("admin.seasons"))
    Season.query.filter_by(is_active=True).update({"is_active": False})
    season.is_active = True
    db.session.commit()

    bootstrap_season_roster(season.id)

    flash(f"Season {season.year} is now active.", "success")
    return redirect(url_for("admin.seasons"))


@bp.get("/seasons/<int:season_id>/roster")
@login_required
def season_roster(season_id):
    season = Season.query.get_or_404(season_id)
    bootstrap_season_roster(season.id)  # covers users created after the season started

    user_seasons = {
        us.user_id: us
        for us in UserSeason.query.filter_by(season_id=season.id).all()
    }
    users = User.query.order_by(User.username.asc()).all()
    rows = [
        {"user": u, "user_season": user_seasons[u.id]}
        for u in users if u.id in user_seasons
    ]
    return render_template("season_roster.html", season=season, rows=rows)


@bp.post("/seasons/<int:season_id>/roster/<int:user_id>/toggle-playing")
@login_required
def toggle_roster_playing(season_id, user_id):
    us = get_or_create_user_season(user_id, season_id)
    us.is_playing = not us.is_playing
    db.session.commit()
    return redirect(url_for("admin.season_roster", season_id=season_id))


@bp.post("/seasons/<int:season_id>/roster/<int:user_id>/toggle-paid")
@login_required
def toggle_roster_paid(season_id, user_id):
    us = get_or_create_user_season(user_id, season_id)
    us.entry_paid = not us.entry_paid
    db.session.commit()
    return redirect(url_for("admin.season_roster", season_id=season_id))


@bp.post("/seasons/<int:season_id>/set-start-date")
@login_required
def set_season_start_date(season_id):
    season = Season.query.get_or_404(season_id)
    start_date_str = (request.form.get("start_date") or "").strip()
    try:
        season.start_date = date.fromisoformat(start_date_str)
    except ValueError:
        flash("Start date must be YYYY-MM-DD.", "error")
        return redirect(url_for("admin.seasons"))
    db.session.commit()
    flash(f"Season {season.year} start date set to {season.start_date}.", "success")
    return redirect(url_for("admin.seasons"))
