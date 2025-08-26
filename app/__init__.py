from flask import Flask, request
from flask_login import current_user
from config import get_config
from datetime import datetime, timezone
from app.extensions import db, migrate, login_manager
from app.filters import register_template_utils, abbr_team
from app.models import Game, Pick, TeamGameATS
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
    from app.admin import bp as admin_bp; app.register_blueprint(admin_bp, url_prefix="/admin")
    from app.standings import bp as standings_bp; app.register_blueprint(standings_bp)
    from app.weekly_lines import bp as weekly_lines_bp; app.register_blueprint(weekly_lines_bp)
    from app.users import bp as users_bp; app.register_blueprint(users_bp)
    from app.auth import bp as auth_bp; app.register_blueprint(auth_bp, url_prefix="/auth")
    
    def _resolve_footer_week():
        # Prefer explicit ?week=; else latest published (locked) week; else None
        w = request.args.get("week", type=int)
        if w is not None:
            return w
        row = (db.session.query(Game.week)
            .filter(Game.spread_is_locked.is_(True))
            .order_by(Game.week.desc())
            .first())
        return row[0] if row else None

    @app.context_processor
    def inject_footer_picks():
        week = _resolve_footer_week()
        picks = []
        if current_user.is_authenticated and week is not None:
            rows = (db.session.query(Pick, Game)
                    .join(Game, Pick.game_id == Game.id)
                    .filter(Pick.user_id == current_user.id, Game.week == week)
                    .order_by(Game.kickoff_at.asc(), Game.id.asc())
                    .all())
            game_ids = [g.id for _, g in rows]
            ats_rows = (TeamGameATS.query
                        .filter(TeamGameATS.game_id.in_(game_ids)).all()) if game_ids else []
            ats = {(r.game_id, r.team): (r.ats_result or None) for r in ats_rows}

            for p, g in rows:
                picks.append({
                    "abbr": abbr_team(p.chosen_team),
                    "ats":  ats.get((g.id, p.chosen_team)),  # 'COVER' | 'NO_COVER' | 'PUSH' | None
                })

        return dict(
            footer_selected_week=week,          # int or None
            footer_committed_picks=picks,       # list of {abbr, ats}
        )

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
