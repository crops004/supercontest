import os
from dotenv import load_dotenv
from datetime import timedelta

# Load .env only for local/dev convenience. In production (Render), env vars come from the dashboard.
load_dotenv()

def _normalized_db_url(raw: str | None) -> str | None:
    if not raw:
        return None
    # SQLAlchemy prefers postgresql://, some providers give postgres://
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql://", 1)
    return raw

class BaseConfig:
    # --- Secrets & keys ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-key")  # override in production

    # --- Database ---
    SQLALCHEMY_DATABASE_URI = _normalized_db_url(os.environ.get("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Keep DB connections healthy on platforms with aggressive TCP timeouts
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- Sessions / Cookies (hardened defaults; can be relaxed in dev) ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # --- Odds API settings ---
    ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
    ODDS_BASE_URL = "https://api.the-odds-api.com/v4"
    ODDS_REGIONS = os.environ.get("ODDS_REGIONS", "us")
    # strip() each key in case .env had spaces after commas
    ODDS_BOOKMAKERS = [b.strip() for b in os.environ.get("ODDS_BOOKMAKERS", "draftkings").split(",")]
    ODDS_ODDS_FORMAT = os.environ.get("ODDS_ODDS_FORMAT", "american")
    ODDS_DATE_FORMAT = os.environ.get("ODDS_DATE_FORMAT", "iso")
    DEFAULT_BOOKMAKER_KEY = os.environ.get("DEFAULT_BOOKMAKER_KEY", "draftkings")
    ODDS_TIMEOUT_SECONDS = int(os.environ.get("ODDS_TIMEOUT_SECONDS", 20))

    # Optional: example of reading your custom timestamp if you need it in code
    NFL_WEEK1_THURSDAY_UTC = os.environ.get("NFL_WEEK1_THURSDAY_UTC")

class DevelopmentConfig(BaseConfig):
    DEBUG = True
    # If no DB URL provided locally, fall back to a SQLite file so the app still boots
    if BaseConfig.SQLALCHEMY_DATABASE_URI is None:
        SQLALCHEMY_DATABASE_URI = "sqlite:///dev.db"
    # Dev cookies don’t need to be secure
    SESSION_COOKIE_SECURE = False

class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False
    # Secure cookies in prod
    SESSION_COOKIE_SECURE = True
    # If you use url_for(..., _external=True) behind Render’s proxy, prefer https links
    PREFERRED_URL_SCHEME = "https"

def get_config():
    """Choose config based on FLASK_ENV (or APP_ENV). Default to Production."""
    env = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "production").lower()
    if env.startswith("dev"):
        return DevelopmentConfig
    return ProductionConfig