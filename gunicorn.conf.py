"""Gunicorn settings — loaded automatically when gunicorn starts in this folder.

Values given on the command line (e.g. Render's start command) or in GUNICORN_CMD_ARGS
still take priority.
"""

import os

# Bind to Render's port when no --bind is given.
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# One process with several threads. A thread waiting on the database or building a PDF no
# longer makes every other phone wait, and memory stays that of one process (the small Render
# plan has little). Only one process runs the start-up data steps. Change with
# GUNICORN_WORKERS / GUNICORN_THREADS if the plan grows.
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Load the app (and run the start-up data steps) once in the master process; a worker that
# is ever restarted then starts instantly instead of repeating the start-up work.
# (Render also passes --preload in GUNICORN_CMD_ARGS.)
preload_app = True

# Close the connection after each response, exactly as the old "sync" worker did. Render's
# proxy keeps idle connections open longer than gunicorn would, and a connection closed by
# gunicorn just as the proxy reuses it shows up as a 502.
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "0"))

# A killed worker drops the browser's connection, which phones show as "The network
# connection was lost". With threads a slow request (big PDF, report, first query after a
# cold start) no longer gets its worker killed at all: this timeout now only restarts a
# worker that has hung completely. Kept at 2 minutes (the default was 30 seconds).
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30


def _dispose_db_pool(flask_app, close):
    try:
        from app import db

        with flask_app.app_context():
            db.engine.dispose(close=close)
    except Exception:  # noqa: BLE001 - never stop gunicorn over this
        pass


def when_ready(server):
    # The master process only used the database for the start-up steps: close those
    # connections so they are not shared with the worker forked from it.
    flask_app = getattr(server.app, "callable", None)
    if flask_app is not None:
        _dispose_db_pool(flask_app, close=True)


def post_fork(server, worker):
    # Each worker starts with an empty connection pool of its own (SQLAlchemy's advice for
    # forked processes); close=False leaves anything inherited from the master untouched.
    flask_app = getattr(worker.app, "callable", None)
    if flask_app is not None:
        _dispose_db_pool(flask_app, close=False)
