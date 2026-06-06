from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user

from app import db
from app.models import User
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
            log_audit(user.id, "LOGIN", "User", user.id, f"User {username} logged in")
            db.session.commit()
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main.dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    from flask_login import current_user

    log_audit(current_user.id, "LOGOUT", "User", current_user.id, "User logged out")
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
