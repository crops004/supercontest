from decimal import Decimal

from app.services.ats import _compute_ats


def test_cover():
    result, margin = _compute_ats(points_for=24, points_against=17, closing_spread=Decimal("-3.5"))
    assert result == "COVER"
    assert margin == Decimal("3.5")


def test_no_cover():
    result, margin = _compute_ats(points_for=17, points_against=24, closing_spread=Decimal("3.5"))
    assert result == "NO_COVER"
    assert margin == Decimal("-3.5")


def test_push():
    result, margin = _compute_ats(points_for=20, points_against=17, closing_spread=Decimal("-3"))
    assert result == "PUSH"
    assert margin == Decimal("0")
