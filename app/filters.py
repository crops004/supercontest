from __future__ import annotations
from typing import Optional, Any, cast
from decimal import Decimal
from app.models import Game
from app.services.time_utils import to_local, round5
from datetime import timezone
from app import db


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

ABBR_TO_CITY = {
    # NFC
    "ARI":"Arizona","ATL":"Atlanta","CAR":"Carolina","CHI":"Chicago","DAL":"Dallas",
    "DET":"Detroit","GB":"Green Bay","LAR":"Los Angeles","MIN":"Minnesota","NO":"New Orleans",
    "NYG":"New York","PHI":"Philadelphia","SF":"San Francisco","SEA":"Seattle","TB":"Tampa Bay",
    "WAS":"Washington",
    # AFC
    "BAL":"Baltimore","BUF":"Buffalo","CIN":"Cincinnati","CLE":"Cleveland","DEN":"Denver",
    "HOU":"Houston","IND":"Indianapolis","JAX":"Jacksonville","KC":"Kansas City","LV":"Las Vegas",
    "LAC":"Los Angeles","MIA":"Miami","NE":"New England","NYJ":"New York","PIT":"Pittsburgh","TEN":"Tennessee",
}

# Optional: nickname -> city convenience (covers single-word inputs like "Eagles")
NICK_TO_CITY = {
    "Cardinals":"Arizona","Falcons":"Atlanta","Panthers":"Carolina","Bears":"Chicago","Cowboys":"Dallas",
    "Lions":"Detroit","Packers":"Green Bay","Rams":"Los Angeles","Vikings":"Minnesota","Saints":"New Orleans",
    "Giants":"New York","Eagles":"Philadelphia","49ers":"San Francisco","Seahawks":"Seattle","Buccaneers":"Tampa Bay",
    "Commanders":"Washington","Football Team":"Washington","Redskins":"Washington",
    "Ravens":"Baltimore","Bills":"Buffalo","Bengals":"Cincinnati","Browns":"Cleveland","Broncos":"Denver",
    "Texans":"Houston","Colts":"Indianapolis","Jaguars":"Jacksonville","Chiefs":"Kansas City","Raiders":"Las Vegas",
    "Chargers":"Los Angeles","Dolphins":"Miami","Patriots":"New England","Jets":"New York","Steelers":"Pittsburgh","Titans":"Tennessee",
}

# ---- Filters ----
def _as_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def _get(obj, name):
    """Get attr or key from obj (works for dataclasses/ORM models AND dicts)."""
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

def _ats(side: str, h: float, a: float, sh: float, sa: float) -> str:
    adj, opp = (h + sh, a) if side == "home" else (a + sa, h)
    if adj > opp:  return "win"
    if adj < opp:  return "loss"
    return "push"

def chip_class_ats_computed(pick) -> str:
    """
    Compute 'win'|'loss'|'push'|'pending' for a footer pick.
    Works with dict-based footer payloads or ORM objects.
    """
    # Game object/dict (prefer the payload; fallback to DB by id)
    g = _get(pick, "game")
    if not g:
        gid = _get(pick, "game_id")
        if gid is None:
            return "pending"
        g = db.session.get(Game, int(gid))
        if not g:
            return "pending"

    # Read numbers from object OR dict
    h_f  = _as_float(_get(g, "final_score_home"))
    a_f  = _as_float(_get(g, "final_score_away"))
    sh_f = _as_float(_get(g, "spread_home"))
    sa_f = _as_float(_get(g, "spread_away"))
    if None in (h_f, a_f, sh_f, sa_f):
        return "pending"

    # Team identities
    home_name = _get(g, "home_team")
    away_name = _get(g, "away_team")
    home_abbr = abbr_team(home_name) if home_name else None
    away_abbr = abbr_team(away_name) if away_name else None

    # What the pick says
    pick_abbr = _get(pick, "abbr")
    pick_team = _get(pick, "chosen_team") or _get(pick, "team") or _get(pick, "team_name")
    side = None

    # Resolve side robustly
    if pick_abbr and pick_abbr in (home_abbr, away_abbr):
        side = "home" if pick_abbr == home_abbr else "away"
    elif pick_team:
        pt = str(pick_team).strip()
        if pt in (home_name, away_name):
            side = "home" if pt == home_name else "away"
        else:
            pt_abbr = abbr_team(pt)
            if pt_abbr and pt_abbr in (home_abbr, away_abbr):
                side = "home" if pt_abbr == home_abbr else "away"

    if not side:
        return "pending"

    h  = cast(float, h_f)
    a  = cast(float, a_f)
    sh = cast(float, sh_f)
    sa = cast(float, sa_f)

    return _ats(side, h, a, sh, sa)

def team_short(name: Optional[str]) -> str:
    """Last word of team name ('Washington Commanders' -> 'Commanders')."""
    if not name:
        return ""
    return name.split()[-1]

def abbr_team(name: Optional[str]) -> str:
    if not name:
        return ""
    n = name.strip()

    # 1) exact match (full name or nickname if you add those keys)
    code = TEAM_ABBR.get(n)
    if code:
        return code

    # 2) nickname (last word) -> e.g., "Kansas City Chiefs" -> "Chiefs"
    last = n.rsplit(" ", 1)[-1]
    code = TEAM_ABBR.get(last)
    if code:
        return code

    # 3) city initials (all words except last/nickname) -> "Kansas City Chiefs" -> "KC"
    parts = [p for p in n.split() if p]           # split words
    if len(parts) >= 2:
        city_initials = "".join(w[0] for w in parts[:-1]).upper()
        # normalize some common two-word cities where you prefer 2-letter city abbrs
        return city_initials  # e.g., KC, NY, LA, SF, TB, NE, GB, NO, LV, etc.

    # 4) old fallback
    return n[:3].upper()

def fmt_spread(value) -> str:
    """
    Formats a point spread with a leading sign:
    -3.5 stays "-3.5", +3.5 shows as "+3.5", integers drop .0 ("+3" / "-3").
    """
    if value is None or value == "":
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        # If it's already a label like "PK" or "EVEN", pass it through
        return str(value).strip()

    # If you prefer PK for zero, swap the next line for: return "PK"
    if abs(v) < 1e-9:
        return "0"

    if v.is_integer():
        return f"{v:+.0f}"   # "+3" or "-3"
    return f"{v:+.1f}"       # "+3.5" or "-3.5"

def team_city(name: Optional[str]) -> str:
    """
    Return the full city/region part of a team name.
    Examples:
      "Philadelphia Eagles" -> "Philadelphia"
      "Los Angeles Chargers" -> "Los Angeles"
      "Eagles" -> "Philadelphia" (via NICK_TO_CITY)
      "PHI" -> "Philadelphia" (via ABBR_TO_CITY)
    """
    if not name:
        return ""
    n = name.strip()

    # Abbreviation input (e.g., "PHI", "LAR")
    if n.isupper() and len(n) <= 4:
        return ABBR_TO_CITY.get(n, n)

    parts = n.split()
    if len(parts) >= 2:
        # Full name: everything except the last token (nickname) is the city/region
        return " ".join(parts[:-1])

    # Single word input like "Eagles"
    return NICK_TO_CITY.get(n, n)

# ----- Chip helpers -----
# Map both internal result labels and ATS strings to a canonical class key
RESULT_TO_CLASS = {
    "win": "win",
    "loss": "loss",
    "push": "push",
    "pending": "pending",
    None: "pending",
    # ATS -> canonical
    "COVER": "win",
    "NO_COVER": "loss",
    "PUSH": "push",
}
def chip_class(result: Optional[str]) -> str:
    """Return 'win' | 'loss' | 'push' | 'pending' for a given result/ATS value."""
    return RESULT_TO_CLASS.get(result, "pending")

def chip_tw(result: Optional[str]) -> str:
    """Tailwind classes for a chip based on result/ATS value."""
    palette = {
        "win":     "border-green-200 bg-green-50 text-green-700",
        "loss":    "border-red-200 bg-red-50 text-red-700",
        "push":    "border-amber-200 bg-amber-50 text-amber-800",
        "pending": "border-slate-200 bg-slate-50 text-slate-500",
    }
    return palette.get(chip_class(result), palette["pending"])

def fmt_local(dt_utc, tzname="UTC", kind="time"):
    if not dt_utc:
        return ""
    local = to_local(dt_utc, tzname)
    if kind == "time":
        local = round5(local)
        h = local.strftime('%I').lstrip('0') or '0'
        m = local.strftime('%M')
        ap = local.strftime('%p')
        abbr = local.strftime('%Z')
        return f"{h}:{m} {ap} {abbr}"
    # kind == "date"  ->  Thursday (9/4)
    dow = local.strftime("%A")
    mm  = local.strftime("%m").lstrip("0") or "0"
    dd  = local.strftime("%d").lstrip("0") or "0"
    return f"{dow} ({mm}/{dd})"

# ---- Globals ----
def is_pickem(game: Game) -> bool:
    """True when both sides are effectively 0."""
    sh, sa = getattr(game, "spread_home", None), getattr(game, "spread_away", None)
    if sh is None or sa is None:
        return False
    return abs(float(sh)) < 1e-9 and abs(float(sa)) < 1e-9

def to_utc_ts(dt):
    """
    Convert a datetime (naive or tz-aware) to a UTC epoch timestamp (float).
    Returns None if dt is falsy.
    """
    if not dt:
        return None
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.timestamp()

# ---- Registration hook ----
def register_template_utils(app):
    # Filters
    app.add_template_filter(team_short, "team_short")
    app.add_template_filter(abbr_team,  "abbr_team")
    app.add_template_filter(fmt_spread, "fmt_spread")
    app.add_template_filter(fmt_local, "fmt_local")
    app.add_template_filter(chip_class, "chip_class")
    app.add_template_filter(chip_tw,    "chip_tw")  
    app.add_template_filter(team_city,  "team_city")
    app.add_template_filter(to_utc_ts,  "to_utc_ts")     
    app.add_template_filter(chip_class_ats_computed, "chip_class_ats_computed")
    # Globals
    app.add_template_global(is_pickem,  "is_pickem")
