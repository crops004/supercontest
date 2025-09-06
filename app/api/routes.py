from flask import Blueprint, jsonify, current_app, make_response, url_for, request
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Game
from app.services.picks import remaining_picks_this_week

bp = Blueprint("api", __name__, url_prefix="/api")

def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["Vary"] = "Cookie"
    return resp

@bp.get("/picks/status")
@login_required
def picks_status():
    # Global toggle
    if not current_app.config.get("SHOW_PICKS_BANNER", True):
        payload = {"show": False, "remaining": 0, "current_week": None, "finalized": True}
        resp = make_response(jsonify(payload), 200)
        resp.headers["X-PB-User"] = str(getattr(current_user, "id", "?"))
        resp.headers["X-PB-Remaining"] = "0"
        resp.headers["X-PB-Week"] = "None"
        current_app.logger.info("PB /picks/status DISABLED user=%s", getattr(current_user, "id", "?"))
        return _no_cache(resp)

    picks_per_week = current_app.config.get("PICKS_PER_WEEK", 5)
    remaining, wk = remaining_picks_this_week(current_user.id, picks_per_week)

    payload = {
        "show": remaining > 0,
        "remaining": remaining,
        "current_week": wk,
        "finalized": remaining == 0,
        "link": url_for("weekly_lines.weekly_lines", week=wk) if wk else url_for("weekly_lines.weekly_lines"),
    }

    resp = make_response(jsonify(payload), 200)
    # Debug headers (safe)
    resp.headers["X-PB-User"] = str(getattr(current_user, "id", "?"))
    resp.headers["X-PB-Remaining"] = str(remaining)
    resp.headers["X-PB-Week"] = str(wk)
    _no_cache(resp)

    # Server-side log for quick grep
    current_app.logger.info(
        "PB /picks/status user=%s week=%s remaining=%s show=%s finalized=%s",
        getattr(current_user, "id", "?"), wk, remaining, payload["show"], payload["finalized"]
    )
    return resp

def _current_contest_week() -> int | None:
    """
    Current week = max(Game.week) where kickoff <= now().
    Returns None if no games exist.
    """
    wk = db.session.query(func.max(Game.week)).filter(Game.kickoff_at <= func.now()).scalar()
    return int(wk) if wk is not None else None

@bp.get("/billing/status")
@login_required
def billing_status():
    # If you ever want a global toggle for this, mirror SHOW_PICKS_BANNER handling
    unpaid = not bool(getattr(current_user, "entry_paid", False))
    wk = _current_contest_week() or 1

    # height factor by week
    factor = 1 if wk <= 1 else 2 if wk == 2 else 3 if wk == 3 else 4

    payload = {
        "show": unpaid,
        "current_week": wk,
        "height_factor": factor,
        "link": "https://venmo.com/u/Joe-Cropsey",
    }

    resp = make_response(jsonify(payload), 200)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["Vary"] = "Cookie"
    # Optional debug headers:
    resp.headers["X-Billing-Show"] = "1" if unpaid else "0"
    resp.headers["X-Billing-Week"] = str(wk)
    resp.headers["X-Billing-Factor"] = str(factor)
    return resp
