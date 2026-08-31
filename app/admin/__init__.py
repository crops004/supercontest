from flask import Blueprint, request, abort
from flask_login import current_user

bp = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
    url_prefix="/admin",
)

# Make abbr_team available in ALL admin templates
from app.filters import abbr_team  # <-- verify this path works
bp.add_app_template_global(abbr_team, name="abbr_team")
bp.add_app_template_filter(abbr_team, name="abbr_team")  # so you can also use the |abbr_team filter


@bp.before_request
def _require_admin():
    # Cron endpoints authenticate themselves via X-CRON-TOKEN (no logged-in
    # user involved), so they're exempt from the session-based admin check.
    if request.path.startswith("/admin/internal/cron/"):
        return
    if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
        abort(403)


from . import routes    # noqa: E402,F401  (hub, actions, lock/sync cron, ATS, picks matrix)
from . import emails    # noqa: E402,F401  (email previews/sends + their cron endpoints)
from . import db_admin  # noqa: E402,F401  (generic DB browser/editor)
