from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import app.services.week as week_module
from app.services.week import week_for_kickoff, current_week_number

DENVER = ZoneInfo("America/Denver")


def _set_week1(monkeypatch, start_date=date(2025, 9, 2)):
    """Stub out get_current_season() so week.py doesn't need a real DB/app context."""
    fake_season = SimpleNamespace(start_date=start_date, year=2025)
    monkeypatch.setattr(week_module, "get_current_season", lambda: fake_season)


def test_kickoff_before_week1_start_clamps_to_week1(monkeypatch):
    _set_week1(monkeypatch)
    kickoff = datetime(2025, 8, 20, 18, 0, tzinfo=DENVER)
    assert week_for_kickoff(kickoff) == 1


def test_kickoff_within_first_seven_days_is_week1(monkeypatch):
    _set_week1(monkeypatch)
    kickoff = datetime(2025, 9, 7, 14, 0, tzinfo=DENVER)  # 5 days after start
    assert week_for_kickoff(kickoff) == 1


def test_kickoff_exactly_one_week_later_is_week2(monkeypatch):
    _set_week1(monkeypatch)
    kickoff = datetime(2025, 9, 9, 0, 0, tzinfo=DENVER)  # exactly 7 days after start
    assert week_for_kickoff(kickoff) == 2


def test_kickoff_two_weeks_later_is_week3(monkeypatch):
    _set_week1(monkeypatch)
    kickoff = datetime(2025, 9, 16, 20, 0, tzinfo=DENVER)  # 14 days after start
    assert week_for_kickoff(kickoff) == 3


def test_accepts_iso_string_with_z_suffix(monkeypatch):
    _set_week1(monkeypatch)
    # 2025-09-09T06:00:00Z == 2025-09-09 00:00 Denver (MDT, UTC-6) at that time of year
    assert week_for_kickoff("2025-09-09T06:00:00Z") == 2


def test_current_week_number_uses_given_now(monkeypatch):
    _set_week1(monkeypatch)
    now = datetime(2025, 9, 16, 12, 0, tzinfo=DENVER)
    assert current_week_number(now) == 3


def test_uses_a_different_seasons_own_anchor(monkeypatch):
    # A later season's anchor shifts week numbering independently of 2025's.
    _set_week1(monkeypatch, start_date=date(2026, 9, 8))
    kickoff = datetime(2026, 9, 13, 18, 0, tzinfo=DENVER)  # 5 days after 2026's start
    assert week_for_kickoff(kickoff) == 1


def test_missing_start_date_raises(monkeypatch):
    import pytest
    fake_season = SimpleNamespace(start_date=None, year=2026)
    monkeypatch.setattr(week_module, "get_current_season", lambda: fake_season)
    with pytest.raises(RuntimeError):
        week_for_kickoff(datetime(2026, 9, 13, 18, 0, tzinfo=DENVER))
