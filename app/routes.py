from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app.scoring import points_for_pick, game_result_against_spread
from app.services.time_utils import day_key, time_key
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

# --- ROUTES ---
@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("weekly_lines.weekly_lines"))
    else:
        return redirect(url_for("main.about"))

@bp.route("/about")
def about():
    return render_template("about.html")
