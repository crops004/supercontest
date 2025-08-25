from flask import Blueprint

bp = Blueprint(
    "users",
    __name__,
    template_folder="templates",
)

# Import routes to attach them to the blueprint
from . import routes  # noqa: E402,F401  (register routes)
