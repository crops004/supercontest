from flask import Blueprint

bp = Blueprint(
    "chat",
    __name__,
    template_folder="templates",
    url_prefix="/api/chat",
)

# Import routes to attach them to the blueprint
from . import routes  # noqa: E402,F401  (register routes)
