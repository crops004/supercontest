from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.week import week_for_kickoff, current_week_number

DENVER = ZoneInfo("America/Denver")


def _set_week1(monkeypatch):
    monkeypatch.setenv("NFL_WEEK1_TUESDAY", "2025-09-02")


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
