from . import db
from datetime import datetime

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    week = db.Column(db.Integer, nullable=False)
    home_team = db.Column(db.String(50), nullable=False)
    away_team = db.Column(db.String(50), nullable=False)
    spread = db.Column(db.Float, nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)

    final_score_home = db.Column(db.Integer)
    final_score_away = db.Column(db.Integer)

    def has_started(self):
        return datetime.utcnow() >= self.start_time


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