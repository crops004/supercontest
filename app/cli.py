# app/cli.py
import click
import os
from datetime import datetime, timezone
from flask import current_app
from app import create_app

from app.extensions import db
from app.models import Game

from app.services.odds_client import fetch_odds, fetch_scores, parse_iso_z
from app.services.games_sync import upsert_game_from_odds_event, update_game_scores_from_score_event
from app.services.week_utils import parse_iso_z as parse_iso_z_week, resolve_week1_thursday_utc, week_from_thursday
from app.services.ats import snapshot_closing_lines_for_game, finalize_ats_for_game


def _env_week1_thursday_utc() -> datetime | None:
    v = os.getenv("NFL_WEEK1_THURSDAY_UTC")
    if not v:
        return None
    try:
        return parse_iso_z_week(v)
    except Exception:
        return None

def register_cli(app):
    @app.cli.command("import-preseason")
    def import_preseason():
        """Import preseason odds as Week 0 (DraftKings only)."""
        events = fetch_odds(app.config["SPORT_KEYS"]["preseason"])
        created = updated = 0
        for ev in events:
            existed = Game.query.filter_by(odds_event_id=ev["id"]).one_or_none() is not None
            upsert_game_from_odds_event(ev, force_week=0)  # Week 0
            created += 0 if existed else 1
            updated += 1 if existed else 0
        db.session.commit()
        click.echo(f"Preseason Week 0 upsert complete — created={created}, updated={updated}")

    @app.cli.command("import-regular")
    def import_regular():
        """
        Import regular-season odds and set week based on Week-1 Thursday.
        Uses NFL_WEEK1_THURSDAY_UTC if provided; otherwise infers from payload.
        """
        events = fetch_odds(app.config["SPORT_KEYS"]["regular"])
        wk1 = _env_week1_thursday_utc() or resolve_week1_thursday_utc(events)

        created = updated = 0
        for ev in events:
            existed = Game.query.filter_by(odds_event_id=ev["id"]).one_or_none() is not None

            # Upsert first (no forced week), then set week from kickoff if still 0/None
            game = upsert_game_from_odds_event(ev, force_week=None)

            # Calculate and set week only for regular season; preseason will naturally be < wk1
            try:
                kickoff = parse_iso_z(ev["commence_time"])
                calc_week = week_from_thursday(kickoff, wk1)
                if calc_week > 0:  # positive weeks are regular season
                    game.week = calc_week if game.week in (None, 0) else game.week
            except Exception:
                pass

            if existed: updated += 1
            else: created += 1

        db.session.commit()
        click.echo(f"Regular-season upsert complete — created={created}, updated={updated}, week1_thu_utc={wk1.isoformat()}")

    @app.cli.command("odds-refresh-dk")
    def odds_refresh_dk():
        """
        One-shot: import BOTH preseason (Week 0) and regular-season odds.
        - Preseason events -> Week 0 (forced)
        - Regular events   -> week calculated from Week-1 Thursday
        """
        pre = fetch_odds(app.config["SPORT_KEYS"]["preseason"])
        reg = fetch_odds(app.config["SPORT_KEYS"]["regular"])
        wk1 = _env_week1_thursday_utc() or resolve_week1_thursday_utc(reg)

        c=u=0
        # Preseason first
        for ev in pre:
            existed = Game.query.filter_by(odds_event_id=ev["id"]).one_or_none() is not None
            upsert_game_from_odds_event(ev, force_week=0)
            if existed: u+=1
            else: c+=1
        # Regular
        for ev in reg:
            existed = Game.query.filter_by(odds_event_id=ev["id"]).one_or_none() is not None
            g = upsert_game_from_odds_event(ev, force_week=None)
            try:
                kickoff = parse_iso_z(ev["commence_time"])
                calc_week = week_from_thursday(kickoff, wk1)
                if calc_week > 0:
                    g.week = calc_week if g.week in (None, 0) else g.week
            except Exception:
                pass
            if existed: u+=1
            else: c+=1

        db.session.commit()
        click.echo(f"DK refresh complete — created={c}, updated={u}, week1_thu_utc={wk1.isoformat()}")

    @app.cli.command("update-scores-recent")
    @click.option("--season", type=click.Choice(["pre","reg"], case_sensitive=False), default=None)
    @click.option("--hours", type=int, default=24, help="Converted to daysFrom=ceil(hours/24), clamped 1..3")
    def update_scores_recent_cmd(season, hours):
        """
        Fetch recent scores from the API and update Game.final_score_*.
        NEW: whenever a game's scores change and both sides are set, compute ATS immediately.
        """
        days_from = max(1, min(3, (max(1, hours) + 23) // 24))

        def apply(key: str) -> tuple[int, int]:
            changed = finalized = 0
            data = fetch_scores(key, days_from=days_from)
            by_id = {ev.get("id"): ev for ev in data if ev.get("id")}
            if not by_id:
                return changed, finalized
            games = Game.query.filter(Game.odds_event_id.in_(list(by_id.keys()))).all()
            for g in games:
                before = (g.final_score_home, g.final_score_away)
                update_game_scores_from_score_event(g, by_id[g.odds_event_id])
                after = (g.final_score_home, g.final_score_away)
                if before != after:
                    changed += 1
                    # If both scores present after update, compute ATS now.
                    if after[0] is not None and after[1] is not None:
                        finalize_ats_for_game(g)
                        finalized += 1
            return changed, finalized

        total_changed = total_finalized = 0
        if season == "pre":
            c, f = apply(app.config["SPORT_KEYS"]["preseason"]); total_changed += c; total_finalized += f
        elif season == "reg":
            c, f = apply(app.config["SPORT_KEYS"]["regular"]); total_changed += c; total_finalized += f
        else:
            c, f = apply(app.config["SPORT_KEYS"]["preseason"]); total_changed += c; total_finalized += f
            c, f = apply(app.config["SPORT_KEYS"]["regular"]); total_changed += c; total_finalized += f

        db.session.commit()
        click.echo(f"Scores updated (daysFrom={days_from}): changed={total_changed}, ats_finalized_now={total_finalized}")

    # snapshot closing spreads for all currently locked games
    @app.cli.command("ats-snapshot-locked")
    @click.option("--source", default="Manual/CLI", help="Optional note for where the line came from")
    def ats_snapshot_locked_cmd(source):
        """
        Create or refresh TeamGameATS closing spreads for all games with spread_is_locked = True.
        Safe to run multiple times; it just (re)writes the snapshot.
        """
        games = Game.query.filter_by(spread_is_locked=True).all()
        for g in games:
            snapshot_closing_lines_for_game(g, line_source=source)
        db.session.commit()
        click.echo(f"ATS closing spreads snapshotted for {len(games)} locked games.")

    # one-time or repeatable backfill of ATS for any game with final scores
    @app.cli.command("ats-backfill")
    def ats_backfill_cmd():
        """
        Compute ATS results (COVER/NO_COVER/PUSH) for any game that already has final scores.
        Also snapshots closing spreads if they were missing (fallbacks to current spreads).
        """
        games = Game.query.all()
        snap = fin = 0
        for g in games:
            if getattr(g, 'spread_is_locked', False):
                snapshot_closing_lines_for_game(g, line_source="Backfill")
                snap += 1
            if g.final_score_home is not None and g.final_score_away is not None:
                finalize_ats_for_game(g)
                fin += 1
        db.session.commit()
        click.echo(f"ATS backfill complete — snapshots={snap}, finalized={fin}")