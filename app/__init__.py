from flask import Flask
from config import get_config
from datetime import datetime
from app.extensions import db, migrate, login_manager
import logging
import sys

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

    from . import models  # import models before creating tables
    
    # Blueprints
    from .routes import bp as main_bp
    app.register_blueprint(main_bp)

    from .auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from .admin_routes import admin_bp
    app.register_blueprint(admin_bp)

    from app.leaderboard import bp as leaderboard_bp
    app.register_blueprint(leaderboard_bp)

    # ✅ Register CLI commands
    from .cli import register_cli   # <-- import here
    register_cli(app)               # <-- call here
    
    @app.template_filter("team_short")
    def team_short(name: str) -> str:
        if not name:
            return ""
        return name.split()[-1]  # "Washington Commanders" -> "Commanders"
    
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
        
    return app
