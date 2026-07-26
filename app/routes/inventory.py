from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import InkType, InventoryTransaction
from app.services.ink_inventory import (
    calculate_live_stock,
    calculate_used_from_left,
    create_ink,
    get_current_cans,
    get_ink_options,
    list_inks,
)
from app.services.inventory import log_audit

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@inventory_bp.route("/inks", methods=["GET", "POST"])
@login_required
def inks_list():
    if request.method == "POST":
        require_edit_access()
        ink_company = request.form.get("ink_company", "").strip()
        color_code = request.form.get("color_code", "").strip()
        color = request.form.get("color", "").strip()
        total_cans = request.form.get("total_cans", type=float) or 0
        weight_per_can = request.form.get("weight_per_can", type=float) or 0

        try:
            ink = create_ink(ink_company, color_code, color, total_cans, weight_per_can)
            total_weight = total_cans * weight_per_can
            log_audit(
                current_user.id,
                "CREATE",
                "InkType",
                ink.id,
                f"Ink added: {ink.display_name} ({total_cans} cans, {total_weight:.1f} kg)",
            )
            db.session.commit()
            flash(f"Ink '{ink.display_name}' added.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("inventory.inks_list"))

    inks = list_inks()
    live_stock = {item["ink"].id: item for item in calculate_live_stock()}
    return render_template("ink/inks.html", inks=inks, live_stock=live_stock)


@inventory_bp.route("/cans-in", methods=["GET", "POST"])
@login_required
def cans_in():
    inks = list_inks()

    if request.method == "POST":
        require_edit_access()
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not ink_type_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Ink, number of cans, and date are required.", "danger")
            return redirect(url_for("inventory.cans_in"))

        ink = db.session.get(InkType, ink_type_id)
        if not ink:
            flash("Invalid ink.", "danger")
            return redirect(url_for("inventory.cans_in"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = InventoryTransaction(
            company_id=ink.company_id,
            ink_type_id=ink.id,
            transaction_type=InventoryTransaction.TRANSACTION_CANS_IN,
            quantity=quantity,
            transaction_date=parsed_date,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(txn)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "InventoryTransaction",
            txn.id,
            f"Cans In {quantity} of {ink.display_name}",
        )
        db.session.commit()
        flash(f"Cans In recorded: {quantity} cans of '{ink.display_name}'.", "success")
        return redirect(url_for("inventory.cans_in"))

    recent = (
        InventoryTransaction.query.filter_by(
            transaction_type=InventoryTransaction.TRANSACTION_CANS_IN
        )
        .order_by(InventoryTransaction.transaction_date.desc(), InventoryTransaction.id.desc())
        .limit(20)
        .all()
    )
    return render_template("ink/cans_in.html", inks=inks, recent=recent)


@inventory_bp.route("/cans-left", methods=["GET", "POST"])
@login_required
def cans_left():
    inks = list_inks()

    if request.method == "POST":
        require_edit_access()
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity_left = request.form.get("quantity_left", type=float)
        where_used = request.form.get("where_used", "").strip()
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if ink_type_id is None or quantity_left is None or quantity_left < 0 or not transaction_date:
            flash("Ink, cans left, and date are required.", "danger")
            return redirect(url_for("inventory.cans_left"))

        ink = db.session.get(InkType, ink_type_id)
        if not ink:
            flash("Invalid ink.", "danger")
            return redirect(url_for("inventory.cans_left"))

        try:
            quantity_used = calculate_used_from_left(ink.id, quantity_left)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("inventory.cans_left"))

        if quantity_used <= 0:
            flash("No cans were used — left count matches current stock.", "info")
            return redirect(url_for("inventory.cans_left"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = InventoryTransaction(
            company_id=ink.company_id,
            ink_type_id=ink.id,
            transaction_type=InventoryTransaction.TRANSACTION_CANS_LEFT,
            quantity=quantity_used,
            quantity_left=quantity_left,
            where_used=where_used,
            transaction_date=parsed_date,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(txn)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "InventoryTransaction",
            txn.id,
            f"Cans Left {quantity_left} ({quantity_used} used) of {ink.display_name}",
        )
        db.session.commit()
        flash(
            f"Cans Left recorded: {quantity_used:.1f} cans used, {quantity_left:.1f} cans remaining.",
            "success",
        )
        return redirect(url_for("inventory.cans_left"))

    recent = (
        InventoryTransaction.query.filter_by(
            transaction_type=InventoryTransaction.TRANSACTION_CANS_LEFT
        )
        .order_by(InventoryTransaction.transaction_date.desc(), InventoryTransaction.id.desc())
        .limit(20)
        .all()
    )
    return render_template("ink/cans_left.html", inks=inks, recent=recent)


@inventory_bp.route("/api/inks")
@login_required
def api_inks():
    return jsonify(get_ink_options())


@inventory_bp.route("/api/stock/<int:ink_type_id>")
@login_required
def api_stock(ink_type_id):
    ink = db.session.get(InkType, ink_type_id)
    if not ink:
        return jsonify({"error": "Ink not found"}), 404
    current_cans = get_current_cans(ink)
    return jsonify(
        {
            "current_cans": current_cans,
            "current_weight": current_cans * float(ink.weight_per_can),
            "ink_name": ink.display_name,
        }
    )
