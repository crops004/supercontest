from flask import Blueprint

bp = Blueprint(
    "standings",
    __name__,
    url_prefix="/standings",
    template_folder="templates",
)

# Import routes to attach them to the blueprint
from . import routes  # noqa: E402,F401  (register routes)
