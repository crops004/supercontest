from flask import Blueprint

bp = Blueprint(
    "leaderboard",
    __name__,
    url_prefix="/leaderboard",
    template_folder="templates",
)

from . import routes  # noqa: E402,F401  (register routes)
