from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import Game
from datetime import datetime

bp = Blueprint('main', __name__)

@bp.route('/')
def home():
    return render_template('home.html')

@bp.route('/games')
def list_games():
    games = Game.query.order_by(Game.start_time).all()
    return render_template('games.html', games=games)

@bp.route('/add_game', methods=['GET', 'POST'])
def add_game():
    if request.method == 'POST':
        week = int(request.form['week'])
        home_team = request.form['home_team']
        away_team = request.form['away_team']
        spread = float(request.form['spread'])
        print("RAW start_time from form:", request.form['start_time'])
        try:
            start_time = datetime.strptime(request.form['start_time'], "%Y-%m-%dT%H:%M")
        except ValueError as e:
            flash(f"Invalid datetime format: {e}")
            return redirect(url_for('main.add_game'))


        game = Game(
            week=week, # type: ignore
            home_team=home_team, # type: ignore
            away_team=away_team, # type: ignore
            spread=spread, # type: ignore
            start_time=start_time # type: ignore
        )
        db.session.add(game)
        db.session.commit()
        flash("Game added!")
        return redirect(url_for('main.add_game'))

    return render_template('add_game.html')
