from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user

from app import db
from app.models import User
from app.module_context import (
    MODULE_INK,
    MODULE_MATERIALS,
    clear_active_module,
    get_active_module,
    module_dashboard_url,
    module_label,
    other_module,
    set_active_module,
)
from app.services.inventory import log_audit

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username, is_active=True).first()

        if user and user.check_password(password):
            login_user(user)
            clear_active_module()
            log_audit(user.id, "LOGIN", "User", user.id, f"User {username} logged in")
            db.session.commit()
            next_page = request.args.get("next")
            return redirect(next_page or url_for("auth.choose_module"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/choose-module", methods=["GET", "POST"])
@login_required
def choose_module():
    if request.method == "POST":
        module = request.form.get("module")
        if module in (MODULE_INK, MODULE_MATERIALS):
            set_active_module(module)
            return redirect(module_dashboard_url(module))
        flash("Please choose a valid module.", "danger")

    return render_template("choose_module.html")


@auth_bp.route("/switch-module/<module>")
@login_required
def switch_module(module):
    if module not in (MODULE_INK, MODULE_MATERIALS):
        abort(400)

    set_active_module(module)
    flash(f"Switched to {module_label(module)}.", "info")
    return redirect(module_dashboard_url(module))


@auth_bp.route("/logout")
@login_required
def logout():
    from flask_login import current_user

    log_audit(current_user.id, "LOGOUT", "User", current_user.id, "User logged out")
    db.session.commit()
    logout_user()
    clear_active_module()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
