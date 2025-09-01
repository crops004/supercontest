from flask import Blueprint, jsonify, current_app
from flask_login import login_required, current_user
from app.services.picks import remaining_picks_this_week

bp = Blueprint("api", __name__, url_prefix="/api")

@bp.get("/picks/status")
@login_required
def picks_status():
    # honor global toggle
    if not current_app.config.get("SHOW_PICKS_BANNER", True):
        return jsonify({"show": False, "remaining": 0, "current_week": None, "finalized": True})

    picks_per_week = current_app.config.get("PICKS_PER_WEEK", 5)
    remaining, wk = remaining_picks_this_week(current_user.id, picks_per_week)
    resp = {
        "show": remaining > 0,
        "remaining": remaining,
        "current_week": wk,
        "finalized": remaining == 0,
    }
    return jsonify(resp)
