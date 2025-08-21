from . import db
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from sqlalchemy import Boolean, func, Enum

class User(UserMixin,db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    full_name = db.Column(db.String(120))
    
    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    week = db.Column(db.Integer, nullable=False)
    home_team = db.Column(db.String(50), nullable=False)
    away_team = db.Column(db.String(50), nullable=False)
    final_score_home = db.Column(db.Integer)
    final_score_away = db.Column(db.Integer)
    spread_home = db.Column(db.Numeric, nullable=True)
    spread_away = db.Column(db.Numeric, nullable=True)
    spread_last_update = db.Column(db.DateTime(timezone=True), nullable=True)
    odds_event_id = db.Column(db.Text, index=True, nullable=True)
    kickoff_at = db.Column(db.DateTime(timezone=True), nullable=True)
    spread_is_locked = db.Column(db.Boolean, default=False)
    spread_locked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def has_started(self):
        return datetime.now(timezone.utc) >= self.kickoff_at  # <- aware compare
    
    @property
    def has_final_score(self):
        return self.final_score_home is not None and self.final_score_away is not None

class Pick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    chosen_team = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships (optional)
    user = db.relationship('User', backref='picks', lazy=True)
    game = db.relationship('Game', backref='picks', lazy=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'game_id', name='unique_user_game_pick'),
    )

ATSResultEnum = Enum('COVER', 'NO_COVER', 'PUSH', name='ats_result_enum')

class TeamGameATS(db.Model):
    __tablename__ = 'team_game_ats'

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), index=True, nullable=False)

    # who we’re describing
    team = db.Column(db.String, nullable=False)
    opponent = db.Column(db.String, nullable=False)
    is_home = db.Column(db.Boolean, nullable=False)

    # **snapshot** of the contest line at LOCK time (for THIS team)
    closing_spread = db.Column(db.Numeric(5, 2), nullable=False)  # ex: -3.5 means this team was favored by 3.5
    line_source = db.Column(db.String(64))  # optional: "Manual", "Odds API", etc.

    # filled when the game ends
    points_for = db.Column(db.Integer)
    points_against = db.Column(db.Integer)
    ats_result = db.Column(ATSResultEnum)  # COVER | NO_COVER | PUSH
    cover_margin = db.Column(db.Numeric(5, 2))  # (points_for + closing_spread) - points_against

    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('game_id', 'team', name='uq_team_game_once'),
    )
