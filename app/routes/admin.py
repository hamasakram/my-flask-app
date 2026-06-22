from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import AppSetting, InkType, User
from app.services.inventory import log_audit
from app.services.stock_reset import reset_ink_stock_data, reset_materials_stock_data

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def require_admin():
    if not current_user.is_admin():
        abort(403)


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    require_admin()
    setting = AppSetting.query.filter_by(key="default_low_stock_threshold").first()

    if request.method == "POST":
        threshold = request.form.get("default_low_stock_threshold", type=int)
        if threshold is None or threshold < 0:
            flash("Enter a valid threshold.", "danger")
        else:
            setting.value = str(threshold)
            log_audit(
                current_user.id,
                "UPDATE",
                "AppSetting",
                setting.id,
                f"Default low stock threshold set to {threshold}",
            )
            db.session.commit()
            flash("Settings updated.", "success")
        return redirect(url_for("admin.settings"))

    ink_thresholds = InkType.query.order_by(InkType.company_id, InkType.name).all()
    return render_template(
        "settings.html",
        default_threshold=int(setting.value),
        ink_thresholds=ink_thresholds,
    )


@admin_bp.route("/ink-threshold/<int:ink_id>", methods=["POST"])
@login_required
def update_ink_threshold(ink_id):
    require_admin()
    ink = db.session.get(InkType, ink_id)
    if not ink:
        abort(404)

    threshold = request.form.get("threshold")
    if threshold == "" or threshold is None:
        ink.low_stock_threshold = None
    else:
        ink.low_stock_threshold = int(threshold)

    log_audit(
        current_user.id,
        "UPDATE",
        "InkType",
        ink.id,
        f"Low stock threshold for {ink.name} updated",
    )
    db.session.commit()
    flash(f"Threshold updated for {ink.name}.", "success")
    return redirect(url_for("admin.settings"))


@admin_bp.route("/users", methods=["GET", "POST"])
@login_required
def users():
    require_admin()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", User.ROLE_VIEWER)

        if not username or not password:
            flash("Username and password are required.", "danger")
        elif User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
        elif role not in User.ROLES:
            flash("Invalid role.", "danger")
        else:
            user = User(username=username, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            log_audit(
                current_user.id,
                "CREATE",
                "User",
                user.id,
                f"Created user {username} with role {role}",
            )
            db.session.commit()
            flash("User created.", "success")
        return redirect(url_for("admin.users"))

    all_users = User.query.order_by(User.username).all()
    return render_template("users.html", users=all_users, roles=User.ROLES)


@admin_bp.route("/reset-ink-stock", methods=["POST"])
@login_required
def reset_ink_stock():
    require_admin()
    if request.form.get("confirm") != "RESET INK":
        flash('Type "RESET INK" to confirm clearing all ink stock data.', "danger")
        return redirect(url_for("admin.settings"))

    counts = reset_ink_stock_data()
    log_audit(
        current_user.id,
        "DELETE",
        "StockReset",
        None,
        f"Ink stock reset: {counts}",
    )
    flash(
        f"Ink stock cleared — {counts['opening']} opening, "
        f"{counts['transactions']} transactions, {counts['receipts']} receipts, "
        f"{counts['catalog']} catalog inks removed.",
        "success",
    )
    return redirect(url_for("admin.settings"))


@admin_bp.route("/reset-materials-stock", methods=["POST"])
@login_required
def reset_materials_stock():
    require_admin()
    if request.form.get("confirm") != "RESET MATERIALS":
        flash('Type "RESET MATERIALS" to confirm clearing all materials stock data.', "danger")
        return redirect(url_for("admin.settings"))

    counts = reset_materials_stock_data()
    log_audit(
        current_user.id,
        "DELETE",
        "StockReset",
        None,
        f"Materials stock reset: {counts}",
    )
    flash(
        f"Materials stock cleared — {counts['opening']} opening, "
        f"{counts['transactions']} transactions, {counts['receipts']} receipts, "
        f"{counts['catalog']} catalog materials removed.",
        "success",
    )
    return redirect(url_for("admin.settings"))
