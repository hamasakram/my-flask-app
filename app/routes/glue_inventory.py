from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import Company, GlueItem, GlueOpeningStock, GlueTransaction
from app.services.companies import get_glue_companies
from app.services.glue_chemical_inventory import (
    get_or_create_glue,
    glue_current_stock,
    glue_live_stock,
    glue_used_from_left,
)
from app.services.inventory import log_audit
from app.services.weights import calculate_gross_net

glue_bp = Blueprint("glue", __name__, url_prefix="/glue/inventory")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@glue_bp.route("/companies", methods=["GET", "POST"])
@login_required
def companies():
    if request.method == "POST":
        require_edit_access()
        company_name = request.form.get("company_name", "").strip()

        if not company_name:
            flash("Company name is required.", "danger")
            return redirect(url_for("glue.companies"))

        existing = Company.query.filter_by(name=company_name).first()
        if existing:
            if existing.scope == Company.SCOPE_GLUE:
                flash("This company already exists in Glue.", "warning")
            else:
                flash(
                    "This company name is already used in another module. Choose a different name.",
                    "danger",
                )
            return redirect(url_for("glue.companies"))

        company = Company(name=company_name, scope=Company.SCOPE_GLUE)
        db.session.add(company)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "Company",
            company.id,
            f"Glue company added: {company_name}",
        )
        db.session.commit()
        flash(f"Company '{company_name}' added.", "success")
        return redirect(url_for("glue.companies"))

    return render_template("glue/companies.html", companies=get_glue_companies())


@glue_bp.route("/catalog", methods=["GET", "POST"])
@login_required
def catalog():
    companies = get_glue_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_name = request.form.get("item_name", "").strip()
        unit_type = request.form.get("unit_type", "Kg").strip() or "Kg"

        if not company_id or not item_name:
            flash("Company and item name are required.", "danger")
            return redirect(url_for("glue.catalog"))

        try:
            item = get_or_create_glue(company_id, item_name, unit_type)
            log_audit(
                current_user.id,
                "CREATE",
                "GlueItem",
                item.id,
                f"Glue item saved: {item.display_name}",
            )
            db.session.commit()
            flash(f"Item '{item.display_name}' saved for later use.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        return redirect(url_for("glue.catalog"))

    items = (
        GlueItem.query.join(Company)
        .order_by(Company.name, GlueItem.name)
        .all()
    )
    return render_template(
        "glue/catalog.html",
        companies=companies,
        items=items,
    )


@glue_bp.route("/opening-stock", methods=["GET", "POST"])
@login_required
def opening_stock():
    companies = get_glue_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_id = request.form.get("item_id", type=int)
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not item_id or quantity is None or not as_of_date:
            flash("Company, item, quantity, and date are required.", "danger")
            return redirect(url_for("glue.opening_stock"))

        item = GlueItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item for this company.", "danger")
            return redirect(url_for("glue.opening_stock"))

        parsed_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        existing = GlueOpeningStock.query.filter_by(
            company_id=company_id, item_id=item_id
        ).first()

        if existing:
            existing.quantity = quantity
            existing.as_of_date = parsed_date
            existing.notes = notes
            existing.created_by_id = current_user.id
            action = "UPDATE"
            entity_id = existing.id
        else:
            record = GlueOpeningStock(
                company_id=company_id,
                item_id=item_id,
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
            "GlueOpeningStock",
            entity_id,
            f"{item.display_name}: opening stock set to {quantity}",
        )
        db.session.commit()
        flash("Opening stock saved successfully.", "success")
        return redirect(url_for("glue.opening_stock"))

    records = (
        GlueOpeningStock.query.join(Company)
        .join(GlueItem)
        .order_by(Company.name, GlueItem.name)
        .all()
    )
    return render_template(
        "glue/opening_stock.html",
        companies=companies,
        records=records,
    )


@glue_bp.route("/receive", methods=["GET", "POST"])
@login_required
def receive_stock():
    companies = get_glue_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_id = request.form.get("item_id", type=int)
        quantity = request.form.get("quantity", type=float)
        weight_per_quantity = request.form.get("weight_per_quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not company_id
            or not item_id
            or not quantity
            or quantity <= 0
            or not transaction_date
        ):
            flash("Company, item, valid quantity, and date are required.", "danger")
            return redirect(url_for("glue.receive_stock"))

        item = GlueItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item selection.", "danger")
            return redirect(url_for("glue.receive_stock"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        gross_weight, net_weight = calculate_gross_net(quantity, weight_per_quantity or 0)
        txn = GlueTransaction(
            company_id=company_id,
            item_id=item_id,
            transaction_type=GlueTransaction.TRANSACTION_RECEIVED,
            quantity=quantity,
            weight_per_quantity=weight_per_quantity,
            gross_weight=gross_weight if weight_per_quantity else None,
            net_weight=net_weight if weight_per_quantity else None,
            transaction_date=parsed_date,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(txn)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "GlueTransaction",
            txn.id,
            f"Received {quantity} of {item.display_name} for {txn.company.name}",
        )
        db.session.commit()
        flash(f"Stock received: {quantity} of '{item.display_name}' recorded.", "success")
        return redirect(url_for("glue.receive_stock"))

    recent_received = (
        GlueTransaction.query.filter_by(transaction_type=GlueTransaction.TRANSACTION_RECEIVED)
        .order_by(GlueTransaction.transaction_date.desc(), GlueTransaction.id.desc())
        .limit(30)
        .all()
    )
    return render_template(
        "glue/receive_stock.html",
        companies=companies,
        recent_received=recent_received,
    )


@glue_bp.route("/use", methods=["GET", "POST"])
@login_required
def use_stock():
    companies = get_glue_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_id = request.form.get("item_id", type=int)
        quantity_left = request.form.get("quantity_left", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not company_id
            or not item_id
            or quantity_left is None
            or quantity_left < 0
            or not transaction_date
        ):
            flash("Company, item, quantity left, and date are required.", "danger")
            return redirect(url_for("glue.use_stock"))

        item = GlueItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item selection.", "danger")
            return redirect(url_for("glue.use_stock"))

        try:
            quantity_used = glue_used_from_left(company_id, item_id, quantity_left)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("glue.use_stock"))

        if quantity_used <= 0:
            flash(
                "No stock was used — quantity left matches current stock. Nothing recorded.",
                "info",
            )
            return redirect(url_for("glue.use_stock"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = GlueTransaction(
            company_id=company_id,
            item_id=item_id,
            transaction_type=GlueTransaction.TRANSACTION_USED,
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
            "GlueTransaction",
            txn.id,
            f"Used {quantity_used} of {item.display_name} ({quantity_left} left) for {txn.company.name}",
        )
        db.session.commit()
        flash(
            f"Daily usage recorded: {quantity_used:.1f} used, {quantity_left:.1f} left for '{item.display_name}'.",
            "success",
        )
        return redirect(url_for("glue.use_stock"))

    recent_usage = (
        GlueTransaction.query.filter_by(transaction_type=GlueTransaction.TRANSACTION_USED)
        .order_by(GlueTransaction.transaction_date.desc(), GlueTransaction.id.desc())
        .limit(30)
        .all()
    )
    return render_template(
        "glue/use_stock.html",
        companies=companies,
        recent_usage=recent_usage,
    )


@glue_bp.route("/api/items/<int:company_id>")
@login_required
def get_company_items(company_id):
    items = (
        GlueItem.query.filter_by(company_id=company_id)
        .order_by(GlueItem.name)
        .all()
    )
    return jsonify(
        [
            {"id": i.id, "name": i.display_name, "unit_type": i.unit_type}
            for i in items
        ]
    )


@glue_bp.route("/api/stock/<int:company_id>/<int:item_id>")
@login_required
def get_item_stock(company_id, item_id):
    item = GlueItem.query.filter_by(id=item_id, company_id=company_id).first()
    if not item:
        return jsonify({"error": "Item not found"}), 404

    current = glue_current_stock(company_id, item_id)
    return jsonify({"current_stock": current, "item_name": item.display_name})


@glue_bp.route("/live")
@login_required
def live_inventory():
    company_id = request.args.get("company_id", type=int)
    item_search = request.args.get("item", "").strip().lower()

    rows = glue_live_stock(company_id=company_id)
    if item_search:
        rows = [
            r
            for r in rows
            if item_search in r["item"].display_name.lower()
        ]

    return render_template(
        "glue/live_inventory.html",
        rows=rows,
        companies=get_glue_companies(),
        selected_company=company_id,
        item_search=request.args.get("item", ""),
    )
