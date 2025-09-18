from . import db
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from sqlalchemy import Boolean, func, Enum
from app.extensions import db

class User(UserMixin,db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    first_name = db.Column(db.String(80))
    last_name  = db.Column(db.String(80))
    entry_paid = db.Column(db.Boolean, default=False)
    # Notifications
    notify_lines_posted   = db.Column(db.Boolean, default=True)
    notify_picks_reminder = db.Column(db.Boolean, default=True)
    notify_weekly_recap   = db.Column(db.Boolean, default=True)
    chat_messages = db.relationship("ChatMessage", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)
    
    @property
    def display_full_name(self):
        # prefer first/last; else username
        if self.first_name or self.last_name:
            return (" ".join(p for p in [self.first_name, self.last_name] if p)).strip()
        return self.username

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
    odds_event_id = db.Column(db.Text, unique=True, index=True, nullable=True)
    kickoff_at = db.Column(db.DateTime(timezone=True), nullable=True)
    spread_is_locked = db.Column(db.Boolean, default=False)
    spread_locked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    
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



class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=True, onupdate=func.now())

    __table_args__ = (
        db.Index('ix_chat_messages_created_at', 'created_at'),
    )

    def to_dict(self):
        user = getattr(self, 'user', None)
        display_name = None
        if user is not None:
            display_name = getattr(user, 'display_full_name', None) or getattr(user, 'username', None)
        return {
            'id': self.id,
            'user': {
                'id': user.id if user else None,
                'display_name': display_name or 'Member',
            },
            'body': self.body,
            'created_at': self._iso_timestamp(self.created_at),
            'updated_at': self._iso_timestamp(self.updated_at),
        }

    @staticmethod
    def _iso_timestamp(dt):
        if not dt:
            return None
        if dt.tzinfo is None:
            from datetime import timezone as _tz
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.isoformat().replace('+00:00', 'Z')


class WeeklyEmailLog(db.Model):
    __tablename__ = "weekly_email_log"

    id         = db.Column(db.Integer, primary_key=True)
    week       = db.Column(db.Integer, nullable=False, index=True)
    kind       = db.Column(db.String(32), nullable=False, default="weekly", server_default="weekly")
    subject    = db.Column(db.String(255), nullable=False)
    total      = db.Column(db.Integer, nullable=False, default=0)  # # of intended recipients
    sent       = db.Column(db.Integer, nullable=False, default=0)  # # of successes
    failed     = db.Column(db.Integer, nullable=False, default=0)  # # of failures
    status     = db.Column(db.String(20), nullable=False, default="started")  # started|sent|failed
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    recipients = db.relationship(
        "WeeklyEmailRecipientLog",
        backref="log",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.UniqueConstraint("week", "kind", name="uq_week_kind"),
    )


class WeeklyEmailRecipientLog(db.Model):
    __tablename__ = "weekly_email_recipient_log"

    id         = db.Column(db.Integer, primary_key=True)
    log_id     = db.Column(db.Integer, db.ForeignKey("weekly_email_log.id"), nullable=False, index=True)
    email      = db.Column(db.String(255), nullable=False, index=True)
    status     = db.Column(db.String(20), nullable=False)  # sent|failed
    error      = db.Column(db.Text, nullable=True)         # last error (if any)
    sent_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_recipient_log_logid_email", "log_id", "email"),
    )