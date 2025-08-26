from . import bp
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user, logout_user
from app.extensions import db
from app.models import User
import re

@bp.get("/me")
@login_required
def profile():
    return render_template("profile.html")

@bp.post("/me/notifications")
@login_required
def update_notifications():
    # Checkboxes only submit when checked
    current_user.notify_lines_posted   = "notify_lines_posted"   in request.form
    current_user.notify_picks_reminder = "notify_picks_reminder" in request.form
    current_user.notify_weekly_recap   = "notify_weekly_recap"   in request.form
    db.session.commit()
    flash("Notification settings saved.", "success")
    return redirect(url_for("users.profile"))

# Optional: delete account (be careful with FKs / cascade)
@bp.post("/me/delete")
@login_required
def delete_account():
    uid = current_user.id
    logout_user()
    user = db.session.get(type(current_user), uid)
    db.session.delete(user)
    db.session.commit()
    flash("Account deleted.", "success")
    return redirect(url_for("auth.login"))

@bp.post("/me/update-account")
@login_required
def update_account():
    username   = (request.form.get("username") or "").strip()
    first_name = (request.form.get("first_name") or "").strip()
    last_name  = (request.form.get("last_name") or "").strip()

    errors = []

    # --- username validation ---
    if not username:
        errors.append("Username is required.")
    elif not re.match(r"^[A-Za-z0-9_.-]{3,32}$", username):
        errors.append("Username must be 3–32 chars; letters, numbers, dot, underscore, or hyphen.")
    else:
        exists = User.query.filter(
            User.username == username,
            User.id != current_user.id
        ).first()
        if exists:
            errors.append("That username is already taken.")

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("users.profile"))

    # --- apply updates ---
    current_user.username = username
    if hasattr(current_user, "first_name"):
        current_user.first_name = first_name or None
    if hasattr(current_user, "last_name"):
        current_user.last_name = last_name or None

    db.session.commit()
    flash("Account updated.", "success")
    return redirect(url_for("users.profile"))