from . import bp
from flask import render_template
from flask_login import login_required, current_user

@bp.get("/me")
@login_required
def profile():
    return render_template("profile.html", user=current_user)
