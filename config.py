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
        # Pool size fits gunicorn's threads (gunicorn.conf.py: 1 worker x 4 threads; a POST can
        # hold 2 connections because submit_guard uses its own short transaction).
        # use_lifo reuses the most recently used connection first, so with light traffic one
        # warm connection serves most requests and fewer pre_ping reconnects are needed.
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 280,
            "pool_size": 5,
            "max_overflow": 5,
            "pool_use_lifo": True,
        }
    DEFAULT_LOW_STOCK_THRESHOLD = 50
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    # Files under /static are linked with ?v=<content hash> (see app/__init__.py), so the
    # browser may keep them for a year: a deploy that changes a file changes its URL.
    # Only those versioned /static URLs get this; PDFs, Excel files and uploaded receipts
    # (send_file) keep Flask's default "check with the server every time" behaviour.
    STATIC_FILE_MAX_AGE = 365 * 24 * 60 * 60

    # Response compression (Flask-Compress): pages, CSS, JS and JSON only. PDFs, images and
    # Excel files are already compressed and are left alone. Brotli for Safari/Chrome, gzip
    # for everything else.
    COMPRESS_MIMETYPES = [
        "text/html",
        "text/css",
        "text/plain",
        "text/javascript",
        "application/javascript",
        "application/json",
        "image/svg+xml",
    ]
    COMPRESS_ALGORITHM = ["br", "gzip"]
    COMPRESS_ALGORITHM_STREAMING = ["br", "gzip"]
    COMPRESS_LEVEL = 6
    COMPRESS_BR_LEVEL = 5
    COMPRESS_MIN_SIZE = 500
