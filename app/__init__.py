from flask import Flask, redirect, request, url_for
from flask_login import LoginManager, current_user
from flask_sqlalchemy import SQLAlchemy

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(reports_bp)
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

    @app.context_processor
    def inject_module_context():
        module = get_active_module()
        return {
            "active_module": module,
            "module_label": module_label(module) if module else "",
            "all_modules": all_module_options(module),
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

        if current_user.is_authenticated and current_user.is_dashboard_only():
            if request.endpoint not in dashboard_only_allowed_endpoints():
                return redirect(module_dashboard_url(get_active_module()))

        return None

    with app.app_context():
        db.create_all()
        from app.schema import ensure_schema

        ensure_schema()
        from app.seed import seed_database

        seed_database()

    return app
