# app/services/odds_client.py
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import requests
from flask import current_app

def _get(path: str, params: Dict[str, Any]) -> Any:
    base = current_app.config["ODDS_BASE_URL"]
    timeout = current_app.config.get("ODDS_TIMEOUT_SECONDS", 20)
    merged = {"apiKey": current_app.config["ODDS_API_KEY"], **params}
    r = requests.get(f"{base}{path}", params=merged, timeout=timeout)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        # Optional: surface rate-limit info
        current_app.logger.warning(
            f"Odds API error {r.status_code}; remaining={r.headers.get('x-requests-remaining')} "
            f"reset={r.headers.get('x-requests-reset')}"
        )
        raise
    # Optional: log remaining quota
    rem = r.headers.get("x-requests-remaining")
    if rem is not None:
        current_app.logger.info(f"Odds API remaining={rem}")
    return r.json()

def fetch_odds(sport_key: str) -> Any:
    return _get(
        f"/sports/{sport_key}/odds",
        {
            "regions": current_app.config["ODDS_REGIONS"],
            "oddsFormat": current_app.config["ODDS_ODDS_FORMAT"],
            "bookmakers": ",".join(current_app.config["ODDS_BOOKMAKERS"]),  # DK-only via .env
            "markets": "spreads",
        },
    )

def fetch_scores(sport_key: str, days_from: int = 1) -> Any:
    """
    The Odds API (scores): supports ONLY daysFrom (int 1..3).
    We clamp to that range and send dateFormat=iso for consistency.
    """
    days_from = max(1, min(3, int(days_from)))
    params: Dict[str, Any] = {
        "dateFormat": current_app.config.get("ODDS_DATE_FORMAT", "iso"),
        "daysFrom": days_from,
    }
    return _get(f"/sports/{sport_key}/scores", params)

def parse_iso_z(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(timezone.utc)