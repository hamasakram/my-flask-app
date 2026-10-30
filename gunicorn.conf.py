"""Gunicorn settings — loaded automatically when gunicorn starts in this folder.

Values given on the command line (e.g. Render's start command) still take priority.
"""

import os

# Bind to Render's port when no --bind is given.
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Give slow requests (big PDFs, reports, first query after a cold start) up to 2 minutes
# before the worker is killed. A killed worker drops the browser's connection, which
# phones show as "The network connection was lost". The default was 30 seconds.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
