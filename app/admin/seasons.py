# app/admin/seasons.py
from __future__ import annotations

from datetime import datetime, date
from zoneinfo import ZoneInfo

from flask import request, render_template, redirect, url_for, flash
from flask_login import login_required

from app.extensions import db
from app.models import Season

from . import bp

DENVER = ZoneInfo("America/Denver")


@bp.get("/seasons")
@login_required
def seasons():
    rows = Season.query.order_by(Season.year.desc()).all()
    return render_template("seasons.html", seasons=rows)


@bp.post("/seasons")
@login_required
def create_season():
    year = request.form.get("year", type=int)
    week1_date_str = (request.form.get("week1_date") or "").strip()

    if not year:
        flash("Year is required.", "error")
        return redirect(url_for("admin.seasons"))

    try:
        week1_date = date.fromisoformat(week1_date_str)
    except ValueError:
        flash("Week 1 start date is required (YYYY-MM-DD).", "error")
        return redirect(url_for("admin.seasons"))

    if Season.query.filter_by(year=year).first():
        flash(f"A season for {year} already exists.", "error")
        return redirect(url_for("admin.seasons"))

    week1_anchor = datetime.combine(week1_date, datetime.min.time(), tzinfo=DENVER)

    # Only one season is ever "current" at a time.
    Season.query.filter_by(is_current=True).update({"is_current": False})

    season = Season()
    season.year = year
    season.week1_anchor = week1_anchor
    season.is_current = True
    db.session.add(season)
    db.session.commit()

    flash(f"Season {year} created and set as current.", "success")
    return redirect(url_for("admin.seasons"))


@bp.post("/seasons/<int:season_id>/make-current")
@login_required
def make_season_current(season_id):
    season = Season.query.get_or_404(season_id)
    Season.query.filter_by(is_current=True).update({"is_current": False})
    season.is_current = True
    db.session.commit()
    flash(f"Season {season.year} is now current.", "success")
    return redirect(url_for("admin.seasons"))
