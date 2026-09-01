# app/services/roster.py
from __future__ import annotations

from typing import Optional, Set

from app.extensions import db
from app.models import User, UserSeason


def get_or_create_user_season(user_id: int, season_id: int) -> UserSeason:
    us = UserSeason.query.filter_by(user_id=user_id, season_id=season_id).first()
    if us is None:
        us = UserSeason(user_id=user_id, season_id=season_id, is_playing=True, entry_paid=False)
        db.session.add(us)
        db.session.flush()
    return us


def bootstrap_season_roster(season_id: int, *, default_paid: bool = False) -> None:
    """
    Ensure every existing user has a UserSeason row for this season. Starts
    everyone as not-playing/not-paid - submitting a pick (see
    mark_user_playing) is what actually opts a user into the season; the
    admin roster page can also flip either toggle by hand.
    """
    existing_user_ids = {
        uid for (uid,) in
        db.session.query(UserSeason.user_id).filter_by(season_id=season_id).all()
    }
    for u in User.query.all():
        if u.id in existing_user_ids:
            continue
        db.session.add(UserSeason(
            user_id=u.id, season_id=season_id, is_playing=False, entry_paid=default_paid,
        ))
    db.session.commit()


def mark_user_playing(user_id: int, season_id: int) -> None:
    """Call when a user successfully submits a pick - opts them into that
    season's roster if they weren't already in it."""
    us = get_or_create_user_season(user_id, season_id)
    if not us.is_playing:
        us.is_playing = True


def roster_user_ids(season_id: int) -> Optional[Set[int]]:
    """
    User ids currently playing this season, or None if this season has no
    roster configured yet (treat as "everyone plays" so standings/history
    never silently render empty for a season predating this feature).
    """
    any_row = db.session.query(UserSeason.id).filter_by(season_id=season_id).first()
    if not any_row:
        return None
    rows = (
        db.session.query(UserSeason.user_id)
        .filter_by(season_id=season_id, is_playing=True)
        .all()
    )
    return {uid for (uid,) in rows}
