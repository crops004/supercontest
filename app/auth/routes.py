from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, current_user, login_required
from urllib.parse import urlparse, urljoin
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import hashlib
from sqlalchemy import func

from app.extensions import db
from app.models import User
from app.emailer import send_email
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

def _reset_serializer() -> URLSafeTimedSerializer:
    # Uses your SECRET_KEY; unique salt for this purpose
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="pw-reset")

def _ver_from_user(u: User) -> str:
    # Derives a short version string from password_hash so tokens
    # auto-invalidate after password changes.
    raw = (u.password_hash or "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]

def make_reset_token(u: User) -> str:
    s = _reset_serializer()
    return s.dumps({"id": u.id, "v": _ver_from_user(u)})

def load_user_from_token(token: str, max_age: int = 3600 * 2) -> User | None:
    """Return user if token is valid & unexpired; otherwise None."""
    s = _reset_serializer()
    try:
        data = s.loads(token, max_age=max_age)  # seconds
    except (BadSignature, SignatureExpired):
        return None
    u = User.query.get(data.get("id"))
    if not u:
        return None
    if data.get("v") != _ver_from_user(u):
        return None
    return u

def _is_safe_next_url(target: str) -> bool:
    """Prevent open-redirects: only allow same-host absolute URLs."""
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return (test.scheme in ("http", "https")) and (ref.netloc == test.netloc)

@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("standings.standings"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        # Always show the same success message to avoid account enumeration
        success_msg = "If that email is in our system, you’ll receive a reset link shortly."

        if not email:
            flash(success_msg, "auth_success")
            return redirect(url_for("auth.forgot_password"))

        user = User.query.filter_by(email=email).first()
        if not user:
            flash(success_msg, "auth_success")
            return redirect(url_for("auth.forgot_password"))

        # Build reset link
        token = make_reset_token(user)
        reset_url = url_for("auth.reset_password", token=token, _external=True)

        # Render email bodies
        html = render_template("email/reset_password.html", reset_url=reset_url, user=user)
        text = render_template("email/reset_password.txt", reset_url=reset_url, user=user)

        # Send
        send_email(subject="Reset your password", recipients=email, html=html, text=text)

        flash(success_msg, "auth_success")
        return redirect(url_for("auth.login"))

    return render_template("auth_forgot.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("standings.standings"))

    user = load_user_from_token(token)
    if not user:
        flash("That reset link is invalid or has expired. Please request a new one.", "auth_error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        errors = []
        if not new:
            errors.append("Please enter a new password.")
        if new != confirm:
            errors.append("New password and confirmation do not match.")

        if errors:
            for e in errors:
                flash(e, "auth_error")
            return render_template("auth_reset.html")

        # Save and sign them in (optional)
        user.set_password(new)
        db.session.commit()

        flash("Your password has been updated. Please sign in.", "auth_success")
        return redirect(url_for("auth.login"))

    return render_template("auth_reset.html")

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("standings.standings"))

    if request.method == 'POST':
        identifier = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = None
        if identifier:
            user = User.query.filter_by(username=identifier).first()
            if not user:
                user = User.query.filter(func.lower(User.email) == identifier.lower()).first()

        if not user or not user.check_password(password):
            flash("Invalid username/email or password.", 'auth_error')
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
