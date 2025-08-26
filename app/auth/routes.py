from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user, login_required
from app.extensions import db
from app.models import User
from urllib.parse import urlparse, urljoin
from . import bp

@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("standings.standings"))

    if request.method == 'POST':
        username = request.form.get('username','').strip()
        email = request.form.get('email','').strip()
        password = request.form.get('password','')

        if not username or not password:
            flash("Username and password are required.", 'auth_error')
            return redirect(url_for('auth.register'))

        if User.query.filter_by(username=username).first():
            flash("Username already taken.", 'auth_error')
            return redirect(url_for('auth.register'))

        if email and User.query.filter_by(email=email).first():
            flash("Email already in use.", 'auth_error')
            return redirect(url_for('auth.register'))

        first = request.form.get('first_name','').strip()
        last  = request.form.get('last_name','').strip()
        
        u = User()
        u.username = username
        u.email = email or None
        u.first_name = first
        u.last_name = last
        u.set_password(password)  # sets password_hash
        db.session.add(u)
        db.session.commit()

        flash("Registration successful. Please log in.", 'auth_success')
        return redirect(url_for('auth.login'))

    return render_template('auth_register.html')


def _is_safe_next_url(target: str) -> bool:
    """Prevent open-redirects: only allow same-host absolute URLs."""
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return (test.scheme in ("http", "https")) and (ref.netloc == test.netloc)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("standings.standings"))

    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash("Invalid username or password.", 'auth_error')
            return redirect(url_for('auth.login'))

        login_user(user, remember=remember)

        next_url = request.args.get('next')
        if next_url and _is_safe_next_url(next_url):
            return redirect(next_url)
        # Fallback to standings (or "/" if you mapped standings to root)
        return redirect(url_for('weekly_lines.weekly_lines'))

    return render_template('auth_login.html')

@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new     = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        errors = []

        # Verify current password
        try:
            ok = current_user.check_password(current)  # if you added the helper above
        except AttributeError:
            from werkzeug.security import check_password_hash
            ok = check_password_hash(current_user.password_hash, current)  # adjust field name if needed

        if not ok:
            errors.append("Your current password is incorrect.")

        # Basic strength checks (tweak as you like)
        # if len(new) < 8:
        #     errors.append("New password must be at least 8 characters.")
        # if new.lower() == current.lower():
        #     errors.append("New password must be different from the current one.")
        if new != confirm:
            errors.append("New password and confirmation do not match.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("change_password.html")

        # Save the new password
        try:
            current_user.set_password(new)
        except AttributeError:
            from werkzeug.security import generate_password_hash
            current_user.password_hash = generate_password_hash(new)

        db.session.commit()

        # Optional: log the user out to invalidate other sessions
        return redirect(url_for("users.profile"))

    return render_template("change_password.html")

@bp.route('/logout', endpoint="logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.about"))
