from flask import Blueprint

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

from . import routes  # noqa: E402,F401
