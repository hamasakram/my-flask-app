"""Stops the same form from being saved twice.

safe-submit.js adds a one-time ``_submit_token`` to every POST form. The first request with a
token runs normally; if it ends in a redirect (the usual "saved" response) the redirect target
is remembered. A second request with the same token (double tap, Back + resubmit, or a retry
after "The network connection was lost") does not run the view again: it is sent to the same
place the first one went. Tokens of requests that did not end in a redirect (validation
re-renders, file downloads, errors) are released so the form can be submitted again.

Bookkeeping uses its own short database transactions, never the request's ``db.session``,
so it can't commit or roll back anything a view is doing.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import flash, g, redirect, request
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app import db

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# Logging in / picking a module / switching bank are safe to repeat and must not be skipped.
SKIP_ENDPOINTS = {"auth.login", "auth.choose_module", "sh_main.select_bank", "ping"}
KEEP_DAYS = 3


def _table():
    from app.models import SubmitToken

    return SubmitToken.__table__


def _same_site(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc != request.host:
        return None
    path = parsed.path or "/"
    return path + (f"?{parsed.query}" if parsed.query else "")


def _duplicate_response(location: str | None):
    if location:
        flash("This form was already submitted — showing the result.", "info")
        return redirect(location)
    flash(
        "This form was already submitted and is still being processed. "
        "Check the list before submitting it again.",
        "warning",
    )
    return redirect(_same_site(request.referrer) or "/")


def register(app):
    @app.before_request
    def _guard_duplicate_submit():
        if request.method != "POST" or request.endpoint in SKIP_ENDPOINTS:
            return None
        token = (request.form.get("_submit_token") or "").strip()
        if not TOKEN_RE.match(token):
            return None

        from flask_login import current_user

        table = _table()
        user_id = current_user.get_id() if current_user.is_authenticated else None
        try:
            with db.engine.begin() as conn:
                if random.random() < 0.02:
                    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
                    conn.execute(delete(table).where(table.c.created_at < cutoff))
                existing = conn.execute(
                    select(table.c.location).where(table.c.token == token)
                ).first()
                if existing is None:
                    conn.execute(
                        insert(table).values(
                            token=token,
                            user_id=int(user_id) if user_id else None,
                            endpoint=(request.endpoint or "")[:120],
                            created_at=datetime.now(timezone.utc),
                        )
                    )
                    g.submit_token = token
                    return None
        except IntegrityError:
            with db.engine.begin() as conn:
                existing = conn.execute(
                    select(table.c.location).where(table.c.token == token)
                ).first()
        except Exception:  # bookkeeping must never block a save
            app.logger.exception("submit guard: could not check token")
            return None

        return _duplicate_response(existing[0] if existing else None)

    @app.after_request
    def _remember_submit_result(response):
        token = g.pop("submit_token", None)
        if not token:
            return response
        table = _table()
        try:
            with db.engine.begin() as conn:
                if 300 <= response.status_code < 400 and response.location:
                    location = _same_site(response.location) or "/"
                    conn.execute(
                        update(table).where(table.c.token == token).values(location=location[:500])
                    )
                else:
                    conn.execute(delete(table).where(table.c.token == token))
        except Exception:
            app.logger.exception("submit guard: could not record result")
        return response

    @app.teardown_request
    def _release_on_error(exc):
        token = g.pop("submit_token", None)
        if not token or exc is None:
            return
        try:
            with db.engine.begin() as conn:
                conn.execute(delete(_table()).where(_table().c.token == token))
        except Exception:
            pass
