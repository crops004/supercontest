from __future__ import annotations
from typing import Optional
from app.models import Game  # OK to import models here

TEAM_ABBR = {
    # NFC
    "Cardinals":"ARI","Falcons":"ATL","Panthers":"CAR","Bears":"CHI","Cowboys":"DAL",
    "Lions":"DET","Packers":"GB","Rams":"LAR","Vikings":"MIN","Saints":"NO",
    "Giants":"NYG","Eagles":"PHI","49ers":"SF","Seahawks":"SEA","Buccaneers":"TB",
    "Commanders":"WAS","Football Team":"WAS","Redskins":"WAS",
    # AFC
    "Ravens":"BAL","Bills":"BUF","Bengals":"CIN","Browns":"CLE","Broncos":"DEN",
    "Texans":"HOU","Colts":"IND","Jaguars":"JAX","Chiefs":"KC","Raiders":"LV",
    "Chargers":"LAC","Dolphins":"MIA","Patriots":"NE","Jets":"NYJ","Steelers":"PIT","Titans":"TEN",
    # Common city-only fallbacks (if your Odds API returns these)
    "Washington":"WAS","New York Jets":"NYJ","New York Giants":"NYG","Los Angeles Rams":"LAR",
    "Los Angeles Chargers":"LAC","San Francisco 49ers":"SF","Tampa Bay Buccaneers":"TB",
}

# ---- Filters ----
def team_short(name: Optional[str]) -> str:
    """Last word of team name ('Washington Commanders' -> 'Commanders')."""
    if not name:
        return ""
    return name.split()[-1]

def abbr_team(name: Optional[str]) -> str:
    if not name:
        return ""
    n = name.strip()
    return TEAM_ABBR.get(n, n[:3].upper())

def fmt_spread(value) -> str:
    if value is None:
        return ""
    v = float(value)
    return str(int(v)) if v.is_integer() else f"{v:.1f}"

# Optional: tiny helpers for chips/classes if you want them centralized
RESULT_TO_CLASS = {"win":"win","loss":"loss","push":"push","pending":"pending", None:"pending"}
def chip_class(result: Optional[str]) -> str:
    return RESULT_TO_CLASS.get(result, "pending")

# ---- Globals ----
def is_pickem(game: Game) -> bool:
    """True when both sides are effectively 0."""
    sh, sa = getattr(game, "spread_home", None), getattr(game, "spread_away", None)
    if sh is None or sa is None:
        return False
    return abs(float(sh)) < 1e-9 and abs(float(sa)) < 1e-9

# ---- Registration hook ----
def register_template_utils(app):
    # Filters
    app.add_template_filter(team_short, "team_short")
    app.add_template_filter(abbr_team,  "abbr_team")
    app.add_template_filter(fmt_spread, "fmt_spread")
    app.add_template_filter(chip_class, "chip_class")
    # Globals
    app.add_template_global(is_pickem,  "is_pickem")
