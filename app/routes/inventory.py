from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import Company, InkType, InventoryTransaction, OpeningStock
from app.services.companies import get_ink_companies
from app.services.inventory import (
    calculate_used_from_left,
    get_or_create_ink_type,
    get_stock_usage_records,
    log_audit,
)

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@inventory_bp.route("/opening-stock", methods=["GET", "POST"])
@login_required
def opening_stock():
    companies = get_ink_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        ink_name = request.form.get("ink_name", "").strip()
        color_code = request.form.get("color_code", "").strip()
        unit_type = request.form.get("unit_type", "").strip()
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_name or quantity is None or not as_of_date:
            flash("Company, ink name, quantity, and date are required.", "danger")
            return redirect(url_for("inventory.opening_stock"))

        try:
            ink = get_or_create_ink_type(
                company_id, ink_name, color_code=color_code, unit_type=unit_type
            )
            parsed_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()

            existing = OpeningStock.query.filter_by(
                company_id=company_id, ink_type_id=ink.id
            ).first()

            if existing:
                existing.quantity = quantity
                existing.as_of_date = parsed_date
                existing.notes = notes
                existing.created_by_id = current_user.id
                action = "UPDATE"
                entity_id = existing.id
            else:
                record = OpeningStock(
                    company_id=company_id,
                    ink_type_id=ink.id,
                    quantity=quantity,
                    as_of_date=parsed_date,
                    notes=notes,
                    created_by_id=current_user.id,
                )
                db.session.add(record)
                db.session.flush()
                action = "CREATE"
                entity_id = record.id

            log_audit(
                current_user.id,
                action,
                "OpeningStock",
                entity_id,
                f"{ink.name}: opening stock set to {quantity}",
            )
            db.session.commit()
            flash("Opening stock saved successfully.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        return redirect(url_for("inventory.opening_stock"))

    records = (
        OpeningStock.query.join(Company)
        .join(InkType)
        .order_by(Company.name, InkType.name)
        .all()
    )
    return render_template(
        "opening_stock.html",
        companies=companies,
        records=records,
        unit_types=("Can", "Drum", "Tin"),
    )


@inventory_bp.route("/receive", methods=["GET", "POST"])
@login_required
def receive_stock():
    """Stock Received - user types ink name manually; stored in database."""
    companies = get_ink_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        ink_name = request.form.get("ink_name", "").strip()
        color_code = request.form.get("color_code", "").strip()
        unit_type = request.form.get("unit_type", "").strip()
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_name or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, ink name, valid quantity, and date are required.", "danger")
            return redirect(url_for("inventory.receive_stock"))

        try:
            ink = get_or_create_ink_type(
                company_id, ink_name, color_code=color_code, unit_type=unit_type
            )
            parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()

            txn = InventoryTransaction(
                company_id=company_id,
                ink_type_id=ink.id,
                transaction_type=InventoryTransaction.TRANSACTION_RECEIVED,
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
                f"Received {quantity} of {ink.name} for {txn.company.name}",
            )
            db.session.commit()
            flash(f"Stock received: {quantity} units of '{ink.name}' recorded.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        return redirect(url_for("inventory.receive_stock"))

    return render_template(
        "receive_stock.html",
        companies=companies,
        unit_types=("Can", "Drum", "Tin"),
    )


@inventory_bp.route("/use", methods=["GET", "POST"])
@login_required
def use_stock():
    companies = get_ink_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity_left = request.form.get("quantity_left", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not company_id
            or not ink_type_id
            or quantity_left is None
            or quantity_left < 0
            or not transaction_date
        ):
            flash("Company, ink, quantity left, and date are required.", "danger")
            return redirect(url_for("inventory.use_stock"))

        ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
        if not ink:
            flash("Invalid ink selection for this company.", "danger")
            return redirect(url_for("inventory.use_stock"))

        try:
            quantity_used = calculate_used_from_left(company_id, ink_type_id, quantity_left)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("inventory.use_stock"))

        if quantity_used <= 0:
            flash(
                "No stock was used — quantity left matches current stock. Nothing recorded.",
                "info",
            )
            return redirect(url_for("inventory.use_stock"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = InventoryTransaction(
            company_id=company_id,
            ink_type_id=ink_type_id,
            transaction_type=InventoryTransaction.TRANSACTION_USED,
            quantity=quantity_used,
            quantity_left=quantity_left,
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
            f"Used {quantity_used} of {ink.name} ({quantity_left} left) for {txn.company.name}",
        )
        db.session.commit()
        flash(
            f"Daily usage recorded: {quantity_used:.1f} used, {quantity_left:.1f} left for '{ink.name}'.",
            "success",
        )
        return redirect(url_for("inventory.use_stock"))

    recent_usage = get_stock_usage_records()
    return render_template(
        "use_stock.html",
        companies=companies,
        recent_usage=recent_usage,
    )


@inventory_bp.route("/api/stock/<int:company_id>/<int:ink_type_id>")
@login_required
def get_ink_stock(company_id, ink_type_id):
    from flask import jsonify

    from app.services.inventory import get_current_stock

    ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
    if not ink:
        return jsonify({"error": "Ink not found"}), 404

    current = get_current_stock(company_id, ink_type_id)
    return jsonify({"current_stock": current, "ink_name": ink.name})


@inventory_bp.route("/api/inks/<int:company_id>")
@login_required
def get_company_inks(company_id):
    from flask import jsonify

    inks = (
        InkType.query.filter_by(company_id=company_id)
        .order_by(InkType.name)
        .all()
    )
    return jsonify([{"id": ink.id, "name": ink.name} for ink in inks])


@inventory_bp.route("/live")
@login_required
def live_inventory():
    from app.services.inventory import calculate_live_stock

    company_id = request.args.get("company_id", type=int)
    ink_search = request.args.get("ink", "").strip().lower()

    rows = calculate_live_stock(company_id=company_id)
    if ink_search:
        rows = [r for r in rows if ink_search in r["ink_type"].name.lower()]

    companies = get_ink_companies()
    return render_template(
        "live_inventory.html",
        rows=rows,
        companies=companies,
        selected_company=company_id,
        ink_search=request.args.get("ink", ""),
    )
