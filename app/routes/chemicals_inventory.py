from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import ChemicalItem, ChemicalOpeningStock, ChemicalTransaction, Company
from app.services.companies import get_chemical_companies
from app.services.glue_chemical_inventory import (
    chemical_current_stock,
    chemical_live_stock,
    chemical_used_from_left,
    get_or_create_chemical,
)
from app.services.inventory import log_audit

chemicals_bp = Blueprint("chemicals", __name__, url_prefix="/chemicals/inventory")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@chemicals_bp.route("/companies", methods=["GET", "POST"])
@login_required
def companies():
    if request.method == "POST":
        require_edit_access()
        company_name = request.form.get("company_name", "").strip()

        if not company_name:
            flash("Company name is required.", "danger")
            return redirect(url_for("chemicals.companies"))

        existing = Company.query.filter_by(name=company_name).first()
        if existing:
            if existing.scope == Company.SCOPE_CHEMICALS:
                flash("This company already exists in Chemicals.", "warning")
            else:
                flash(
                    "This company name is already used in another module. Choose a different name.",
                    "danger",
                )
            return redirect(url_for("chemicals.companies"))

        company = Company(name=company_name, scope=Company.SCOPE_CHEMICALS)
        db.session.add(company)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "Company",
            company.id,
            f"Chemicals company added: {company_name}",
        )
        db.session.commit()
        flash(f"Company '{company_name}' added.", "success")
        return redirect(url_for("chemicals.companies"))

    return render_template("chemicals/companies.html", companies=get_chemical_companies())


@chemicals_bp.route("/catalog", methods=["GET", "POST"])
@login_required
def catalog():
    companies = get_chemical_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_name = request.form.get("item_name", "").strip()
        unit_type = request.form.get("unit_type", "Kg").strip() or "Kg"

        if not company_id or not item_name:
            flash("Company and item name are required.", "danger")
            return redirect(url_for("chemicals.catalog"))

        try:
            item = get_or_create_chemical(company_id, item_name, unit_type)
            log_audit(
                current_user.id,
                "CREATE",
                "ChemicalItem",
                item.id,
                f"Chemical item saved: {item.display_name}",
            )
            db.session.commit()
            flash(f"Item '{item.display_name}' saved for later use.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        return redirect(url_for("chemicals.catalog"))

    items = (
        ChemicalItem.query.join(Company)
        .order_by(Company.name, ChemicalItem.name)
        .all()
    )
    return render_template(
        "chemicals/catalog.html",
        companies=companies,
        items=items,
    )


@chemicals_bp.route("/opening-stock", methods=["GET", "POST"])
@login_required
def opening_stock():
    companies = get_chemical_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_id = request.form.get("item_id", type=int)
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not item_id or quantity is None or not as_of_date:
            flash("Company, item, quantity, and date are required.", "danger")
            return redirect(url_for("chemicals.opening_stock"))

        item = ChemicalItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item for this company.", "danger")
            return redirect(url_for("chemicals.opening_stock"))

        parsed_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        existing = ChemicalOpeningStock.query.filter_by(
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
            record = ChemicalOpeningStock(
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
            "ChemicalOpeningStock",
            entity_id,
            f"{item.display_name}: opening stock set to {quantity}",
        )
        db.session.commit()
        flash("Opening stock saved successfully.", "success")
        return redirect(url_for("chemicals.opening_stock"))

    records = (
        ChemicalOpeningStock.query.join(Company)
        .join(ChemicalItem)
        .order_by(Company.name, ChemicalItem.name)
        .all()
    )
    return render_template(
        "chemicals/opening_stock.html",
        companies=companies,
        records=records,
    )


@chemicals_bp.route("/receive", methods=["GET", "POST"])
@login_required
def receive_stock():
    companies = get_chemical_companies()

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
            return redirect(url_for("chemicals.receive_stock"))

        item = ChemicalItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item selection.", "danger")
            return redirect(url_for("chemicals.receive_stock"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = ChemicalTransaction(
            company_id=company_id,
            item_id=item_id,
            transaction_type=ChemicalTransaction.TRANSACTION_RECEIVED,
            quantity=quantity,
            weight_per_quantity=weight_per_quantity,
            transaction_date=parsed_date,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(txn)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ChemicalTransaction",
            txn.id,
            f"Received {quantity} of {item.display_name} for {txn.company.name}",
        )
        db.session.commit()
        flash(f"Stock received: {quantity} of '{item.display_name}' recorded.", "success")
        return redirect(url_for("chemicals.receive_stock"))

    return render_template("chemicals/receive_stock.html", companies=companies)


@chemicals_bp.route("/use", methods=["GET", "POST"])
@login_required
def use_stock():
    companies = get_chemical_companies()

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
            return redirect(url_for("chemicals.use_stock"))

        item = ChemicalItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item selection.", "danger")
            return redirect(url_for("chemicals.use_stock"))

        try:
            quantity_used = chemical_used_from_left(company_id, item_id, quantity_left)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("chemicals.use_stock"))

        if quantity_used <= 0:
            flash(
                "No stock was used — quantity left matches current stock. Nothing recorded.",
                "info",
            )
            return redirect(url_for("chemicals.use_stock"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = ChemicalTransaction(
            company_id=company_id,
            item_id=item_id,
            transaction_type=ChemicalTransaction.TRANSACTION_USED,
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
            "ChemicalTransaction",
            txn.id,
            f"Used {quantity_used} of {item.display_name} ({quantity_left} left) for {txn.company.name}",
        )
        db.session.commit()
        flash(
            f"Daily usage recorded: {quantity_used:.1f} used, {quantity_left:.1f} left for '{item.display_name}'.",
            "success",
        )
        return redirect(url_for("chemicals.use_stock"))

    recent_usage = (
        ChemicalTransaction.query.filter_by(
            transaction_type=ChemicalTransaction.TRANSACTION_USED
        )
        .order_by(ChemicalTransaction.transaction_date.desc(), ChemicalTransaction.id.desc())
        .limit(30)
        .all()
    )
    return render_template(
        "chemicals/use_stock.html",
        companies=companies,
        recent_usage=recent_usage,
    )


@chemicals_bp.route("/api/items/<int:company_id>")
@login_required
def get_company_items(company_id):
    items = (
        ChemicalItem.query.filter_by(company_id=company_id)
        .order_by(ChemicalItem.name)
        .all()
    )
    return jsonify(
        [
            {"id": i.id, "name": i.display_name, "unit_type": i.unit_type}
            for i in items
        ]
    )


@chemicals_bp.route("/api/stock/<int:company_id>/<int:item_id>")
@login_required
def get_item_stock(company_id, item_id):
    item = ChemicalItem.query.filter_by(id=item_id, company_id=company_id).first()
    if not item:
        return jsonify({"error": "Item not found"}), 404

    current = chemical_current_stock(company_id, item_id)
    return jsonify({"current_stock": current, "item_name": item.display_name})


@chemicals_bp.route("/live")
@login_required
def live_inventory():
    company_id = request.args.get("company_id", type=int)
    item_search = request.args.get("item", "").strip().lower()

    rows = chemical_live_stock(company_id=company_id)
    if item_search:
        rows = [
            r
            for r in rows
            if item_search in r["item"].display_name.lower()
        ]

    return render_template(
        "chemicals/live_inventory.html",
        rows=rows,
        companies=get_chemical_companies(),
        selected_company=company_id,
        item_search=request.args.get("item", ""),
    )
