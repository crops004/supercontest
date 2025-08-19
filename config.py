import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key')

    # Odds API settings
    ODDS_API_KEY = os.getenv('ODDS_API_KEY')
    ODDS_BASE_URL = "https://api.the-odds-api.com/v4"
    ODDS_REGIONS = os.getenv('ODDS_REGIONS', 'us')
    ODDS_BOOKMAKERS = os.getenv('ODDS_BOOKMAKERS', 'draftkings').split(',')
    ODDS_ODDS_FORMAT = os.getenv('ODDS_ODDS_FORMAT', 'american')
    ODDS_DATE_FORMAT = os.getenv('ODDS_DATE_FORMAT', 'iso')
    DEFAULT_BOOKMAKER_KEY = os.getenv('DEFAULT_BOOKMAKER_KEY', 'draftkings')
    ODDS_TIMEOUT_SECONDS = int(os.getenv('ODDS_TIMEOUT_SECONDS', 20))

    # NFL sport keys
    SPORT_KEYS = {
        "regular": "americanfootball_nfl",
        "preseason": "americanfootball_nfl_preseason",
    }