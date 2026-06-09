from flask import Flask, redirect, request, url_for
from flask_login import LoginManager, current_user
from flask_sqlalchemy import SQLAlchemy

from config import Config
from app.module_context import (
    MODULE_INK,
    get_active_module,
    module_dashboard_url,
    module_label,
    other_module,
)

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


def create_app(config_class=Config):
    app = Flask(__name__)
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
    from app.routes.reports import reports_bp
    from app.routes.admin import admin_bp
    from app.routes.materials_main import materials_main_bp
    from app.routes.materials_inventory import materials_bp
    from app.routes.materials_reports import materials_reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(materials_main_bp)
    app.register_blueprint(materials_bp)
    app.register_blueprint(materials_reports_bp)

    @app.context_processor
    def inject_module_context():
        module = get_active_module()
        if module:
            other = other_module(module)
            return {
                "active_module": module,
                "module_label": module_label(module),
                "switch_target": other,
                "switch_label": module_label(other),
            }
        return {
            "active_module": None,
            "module_label": "",
            "switch_target": MODULE_INK,
            "switch_label": module_label(MODULE_INK),
        }

    @app.before_request
    def require_module_selection():
        if not current_user.is_authenticated:
            return None

        allowed = {
            "auth.login",
            "auth.logout",
            "auth.choose_module",
            "static",
            None,
        }
        if request.endpoint in allowed:
            return None

        if not get_active_module():
            return redirect(url_for("auth.choose_module"))

        return None

    with app.app_context():
        db.create_all()
        from app.schema import ensure_schema

        ensure_schema()
        from app.seed import seed_database

        seed_database()

    return app
