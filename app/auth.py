from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user, login_required
from app.extensions import db
from app.models import User
from urllib.parse import urlparse, urljoin

bp = Blueprint('auth', __name__)

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
        full_name = f"{first} {last}".strip()
        
        u = User()
        u.username = username
        u.email = email or None
        u.full_name = full_name
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
        flash("Logged in!", 'auth_success')

        next_url = request.args.get('next')
        if next_url and _is_safe_next_url(next_url):
            return redirect(next_url)
        # Fallback to standings (or "/" if you mapped standings to root)
        return redirect(url_for('standings.standings'))

    return render_template('auth_login.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Logged out.", 'auth')
    return redirect(url_for("standings.standings"))
