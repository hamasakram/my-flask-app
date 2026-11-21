import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "rn-colors-stock-dev-key-change-in-production")
    _database_url = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'inventory.db')}"
    )
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    if not _database_url.startswith("sqlite"):
        # The database closes connections that sit idle. Without this the first request
        # after a quiet spell (often the login) used a dead connection and failed with
        # "Internal Server Error". pre_ping tests each connection and reconnects if needed;
        # recycle replaces connections before the server's idle cut-off.
        SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    DEFAULT_LOW_STOCK_THRESHOLD = 50
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
