# app/__init__.py
from flask import Flask, request, current_app, url_for
from flask_login import current_user
from config import get_config
from datetime import datetime, timezone
from app.extensions import db, migrate, login_manager
from app.filters import register_template_utils, abbr_team
from app.models import Game, Pick  # TeamGameATS not needed for footer coloring
from app.services.picks import remaining_picks_this_week
import logging, sys
import os


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    app.config["CRON_SECRET"] = os.getenv("CRON_SECRET", "")
    if not app.config["CRON_SECRET"]:
        app.logger.warning("CRON_SECRET is not set; cron endpoint will return 401")

    # dev-only niceties
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"  # pyright: ignore[reportAttributeAccessIssue] # where to redirect if not logged in

    # Register filters/globals
    register_template_utils(app)

    from . import models  # import models before creating tables

    # Blueprints
    from .routes import bp as main_bp
    app.register_blueprint(main_bp)

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp)

    from app.standings import bp as standings_bp
    app.register_blueprint(standings_bp)

    from app.weekly_lines import bp as weekly_lines_bp
    app.register_blueprint(weekly_lines_bp)

    from app.users import bp as users_bp
    app.register_blueprint(users_bp)

    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix="/auth")

    from app.api.routes import bp as api_bp
    app.register_blueprint(api_bp)

    # ---------------- Footer context (used by base.html) ----------------

    def _resolve_footer_week():
        """
        Prefer explicit ?week= in the URL; otherwise use the most recent locked week.
        Returns an int week or None if no weeks exist yet.
        """
        w = request.args.get("week", type=int)
        if w is not None:
            return w
        row = (
            db.session.query(Game.week)
            .filter(Game.spread_is_locked.is_(True))
            .order_by(Game.week.desc())
            .first()
        )
        return row[0] if row else None

    @app.context_processor
    def inject_footer_picks():
        """
        Injects:
          - footer_selected_week: int|None
          - footer_committed_picks: list of dicts (each includes a minimal 'game' payload)
        This lets the footer color chips from Game scores + spreads without hitting other tables.
        """
        week = _resolve_footer_week()
        picks_payload = []

        if current_user.is_authenticated and week is not None:
            # Join Pick -> Game and pull only what the footer/filter needs
            rows = (
                db.session.query(Pick, Game)
                .join(Game, Pick.game_id == Game.id)
                .filter(Pick.user_id == current_user.id, Game.week == week)
                .order_by(Game.kickoff_at.asc(), Game.id.asc())
                .all()
            )

            for p, g in rows:
                # chosen_team is your picks table column; abbr_team() is your helper
                picks_payload.append(
                    {
                        "id": p.id,
                        "game_id": g.id,
                        "abbr": abbr_team(
                            getattr(p, "chosen_team", None)
                            or getattr(p, "team", None)
                            or getattr(p, "team_name", None)
                            or ""
                        ),
                        "chosen_team": getattr(p, "chosen_team", None)
                        or getattr(p, "team", None)
                        or getattr(p, "team_name", None),
                        "game": {
                            "home_team": g.home_team,
                            "away_team": g.away_team,
                            "final_score_home": g.final_score_home,
                            "final_score_away": g.final_score_away,
                            "spread_home": g.spread_home,
                            "spread_away": g.spread_away,
                        },
                    }
                )

        return dict(
            footer_selected_week=week,
            footer_committed_picks=picks_payload,
        )

    # ---------------- Misc global context ----------------

    @app.context_processor
    def inject_now():
        return {"now": datetime.utcnow}

    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        return User.query.get(int(user_id))

    @app.context_processor
    def inject_picks_banner():
        """
        Exposes `picks_banner` to all templates.
        If anything errors, fail closed (show=False) but log in DEBUG.
        """
        try:
            if current_user.is_authenticated and current_app.config.get(
                "SHOW_PICKS_BANNER", True
            ):
                picks_per_week = int(current_app.config.get("PICKS_PER_WEEK", 5))
                remaining, wk = remaining_picks_this_week(current_user.id, picks_per_week)
                return {
                    "picks_banner": {
                        "show": remaining > 0,
                        "remaining": remaining,
                        "current_week": wk,
                        "link": url_for("weekly_lines.weekly_lines", week=wk),
                    }
                }
        except Exception as e:
            if current_app.debug:
                print("[picks_banner:error]", repr(e))
        return {"picks_banner": {"show": False}}

    # ---------------- Logging & health ----------------

    if not app.debug:  # only tweak for production
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        app.logger.setLevel(logging.INFO)
        app.logger.addHandler(handler)

    @app.get("/healthz")
    def healthz():
        return "ok", 200

    return app
