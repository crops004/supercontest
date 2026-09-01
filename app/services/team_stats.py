# app/services/team_stats.py
from typing import Dict, Tuple

from sqlalchemy import case, func

from app.extensions import db
from app.models import TeamGameATS

Record = Tuple[int, int, int]  # (wins, losses, pushes)


def _add(a: Record, b: Record) -> Record:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def get_team_ats_summary(season_id: int) -> Dict[str, Dict[str, Record]]:
    """
    Returns {team_name: {"overall": (w,l,p), "home": (w,l,p), "away": (w,l,p)}}
    for every team with at least one graded game in the given season.
    """
    covers = func.sum(case((TeamGameATS.ats_result == 'COVER', 1), else_=0))
    pushes = func.sum(case((TeamGameATS.ats_result == 'PUSH', 1), else_=0))
    nocovs = func.sum(case((TeamGameATS.ats_result == 'NO_COVER', 1), else_=0))

    rows = (
        db.session.query(
            TeamGameATS.team,
            TeamGameATS.is_home,
            covers.label("covers"),
            nocovs.label("nocovers"),
            pushes.label("pushes"),
        )
        .filter(TeamGameATS.season_id == season_id)
        .group_by(TeamGameATS.team, TeamGameATS.is_home)
        .all()
    )

    summary: Dict[str, Dict[str, Record]] = {}
    for team, is_home, covers_n, nocovers_n, pushes_n in rows:
        rec: Record = (int(covers_n or 0), int(nocovers_n or 0), int(pushes_n or 0))
        entry = summary.setdefault(team, {"home": (0, 0, 0), "away": (0, 0, 0), "overall": (0, 0, 0)})
        key = "home" if is_home else "away"
        entry[key] = _add(entry[key], rec)
        entry["overall"] = _add(entry["overall"], rec)

    return summary
