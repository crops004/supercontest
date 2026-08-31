from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app.scoring import points_for_pick, game_result_against_spread
from app.services.time_utils import day_key, time_key
from typing import Dict, List, cast
from app.types import AggRow
from datetime import datetime, timezone

from app.extensions import db
from app.models import Game, Pick, User, TeamGameATS

bp = Blueprint('main', __name__)


# --- HELPERS ---
# --- admin access control ---
def admin_required():
    if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
        abort(403)

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
