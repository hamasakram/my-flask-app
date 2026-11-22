from __future__ import annotations

import hashlib

from flask import Flask, has_request_context, redirect, request, url_for
from flask_login import LoginManager, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import safe_join

from config import Config
from app.module_context import (
    ALL_MODULES,
    MODULE_INK,
    all_module_options,
    dashboard_only_allowed_endpoints,
    get_active_module,
    module_dashboard_url,
    module_label,
)

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"

# Requests that never need a chosen module (and never need the logged-in user loaded).
_NO_MODULE_NEEDED = frozenset(
    {
        "auth.login",
        "auth.logout",
        "auth.choose_module",
        "static",
        "ping",
        None,
    }
)


class RNFlask(Flask):
    """Flask with cache-friendly /static files.

    Every ``url_for('static', filename=...)`` gets ``?v=<short hash of the file>``. A URL with
    the file's current hash is served with a one-year cache, so phones stop re-requesting CSS,
    JS and logos on every page; a deploy that changes a file changes its URL. Anything else
    (no/old ``v``, uploaded files, every ``send_file`` download) keeps Flask's default
    "check with the server" behaviour.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._static_versions: dict[str, str] = {}  # filename -> hash, computed once per process

    def static_version(self, filename: str) -> str:
        versions = self._static_versions
        version = versions.get(filename)
        if version is None:
            version = ""
            path = safe_join(self.static_folder, filename) if self.static_folder else None
            if path:
                try:
                    with open(path, "rb") as fh:
                        version = hashlib.sha256(fh.read()).hexdigest()[:10]
                except OSError:
                    version = ""
            versions[filename] = version
        return version

    def _long_cache_static(self, filename: str | None) -> bool:
        if not filename or not has_request_context() or request.endpoint != "static":
            return False
        if filename.replace("\\", "/").startswith("uploads/"):
            return False
        wanted = request.args.get("v")
        return bool(wanted) and wanted == self.static_version(filename)

    def get_send_file_max_age(self, filename):
        if self._long_cache_static(filename):
            return int(self.config.get("STATIC_FILE_MAX_AGE") or 0) or None
        return super().get_send_file_max_age(filename)

    def send_static_file(self, filename):
        response = super().send_static_file(filename)
        if self._long_cache_static(filename) and response.cache_control.max_age:
            response.cache_control.immutable = True
        return response


def create_app(config_class=Config):
    app = RNFlask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.inventory import inventory_bp
    from app.routes.ink_reports import ink_reports_bp
    from app.routes.admin import admin_bp
    from app.routes.materials_main import materials_main_bp
    from app.routes.materials_inventory import materials_bp
    from app.routes.materials_reports import materials_reports_bp
    from app.routes.glue_main import glue_main_bp
    from app.routes.glue_inventory import glue_bp
    from app.routes.chemicals_main import chemicals_main_bp
    from app.routes.chemicals_inventory import chemicals_bp
    from app.routes.sh_main import sh_main_bp
    from app.routes.home_ledger_main import home_ledger_bp
    from app.routes.bank_ledger_main import bank_ledger_bp
    from app.routes.pdf_builder import pdf_bp
    from app.routes.stock_edits import stock_edits_bp
    from app.routes.stock_deletes import stock_deletes_bp
    from app.routes.jobs import jobs_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(ink_reports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(materials_main_bp)
    app.register_blueprint(materials_bp)
    app.register_blueprint(materials_reports_bp)
    app.register_blueprint(glue_main_bp)
    app.register_blueprint(glue_bp)
    app.register_blueprint(chemicals_main_bp)
    app.register_blueprint(chemicals_bp)
    app.register_blueprint(sh_main_bp)
    app.register_blueprint(home_ledger_bp)
    app.register_blueprint(bank_ledger_bp)
    app.register_blueprint(pdf_bp)
    app.register_blueprint(stock_edits_bp)
    app.register_blueprint(stock_deletes_bp)
    app.register_blueprint(jobs_bp)

    @app.url_defaults
    def _static_url_version(endpoint, values):
        if endpoint == "static" and "v" not in values:
            filename = values.get("filename")
            if filename:
                version = app.static_version(filename)
                if version:
                    values["v"] = version

    @app.context_processor
    def inject_module_context():
        module = get_active_module()
        return {
            "active_module": module,
            "module_label": module_label(module) if module else "",
            "all_modules": all_module_options(module),
        }

    @app.route("/ping")
    def ping():
        """Tiny no-cache endpoint used by safe-submit.js to open a fresh connection before a POST."""
        return "", 204, {"Cache-Control": "no-store"}

    @app.before_request
    def require_module_selection():
        # Checked before current_user so /static and /ping never load the user from the database.
        if request.endpoint in _NO_MODULE_NEEDED:
            return None

        if not current_user.is_authenticated:
            return None

        module = get_active_module()
        if not module:
            return redirect(url_for("auth.choose_module"))

        if current_user.is_dashboard_only():
            if request.endpoint not in dashboard_only_allowed_endpoints():
                return redirect(module_dashboard_url(module))

        return None

    # Compression runs after every other after_request hook (it is registered first), so the
    # JSON that submit_guard makes for safe-submit.js is compressed too. Optional: without the
    # package the app still runs, just uncompressed.
    try:
        from flask_compress import Compress
    except ImportError:  # pragma: no cover - only when requirements were not reinstalled
        app.logger.warning("Flask-Compress not installed: responses are sent uncompressed.")
    else:
        Compress(app)

    from app.services.submit_guard import register as register_submit_guard

    register_submit_guard(app)

    with app.app_context():
        db.create_all()
        from app.schema import ensure_schema

        ensure_schema()
        from app.seed import seed_database

        seed_database()

    return app
