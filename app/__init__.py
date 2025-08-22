from flask import Flask
from config import get_config
from datetime import datetime
from app.extensions import db, migrate, login_manager
from app.filters import register_template_utils
import logging, sys

def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    # dev-only niceties
    app.config['TEMPLATES_AUTO_RELOAD'] = True

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'   # pyright: ignore[reportAttributeAccessIssue] # where to redirect if not logged in

    # Register filters/globals in one place
    register_template_utils(app)

    from . import models  # import models before creating tables
    
    # Blueprints
    from .routes import bp as main_bp; app.register_blueprint(main_bp)
    from .auth import bp as auth_bp; app.register_blueprint(auth_bp, url_prefix='/auth')
    from .admin_routes import admin_bp; app.register_blueprint(admin_bp)
    from app.standings import bp as standings_bp; app.register_blueprint(standings_bp)

    @app.context_processor
    def inject_now():
        return {'now': datetime.utcnow}

    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        return User.query.get(int(user_id))

    # --- Logging setup ---
    if not app.debug:  # only tweak for production
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        app.logger.setLevel(logging.INFO)
        app.logger.addHandler(handler)

    # ✅ Health check route (safe, no DB/auth required)
    @app.get("/healthz")
    def healthz():
        return "ok", 200

    return app
