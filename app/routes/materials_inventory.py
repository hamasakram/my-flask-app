from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import Company, Material, MaterialOpeningStock, MaterialTransaction, StockPurchaseReceipt
from app.services.companies import get_material_companies
from app.services.inventory import log_audit
from app.services.receipt_uploads import apply_receipt_file, resolve_receipt_file, save_receipt_upload
from app.services.weights import parse_manual_weights
from app.services.materials_inventory import (
    calculate_live_stock,
    calculate_used_from_left,
    create_material,
    get_company_material_options,
    get_current_stock,
    get_stock_usage_records,
    material_matches_opening_stock,
    resolve_material_selection,
)

materials_bp = Blueprint("materials", __name__, url_prefix="/materials/inventory")
MATERIALS_RECEIPT_DIR = Path("uploads") / "materials" / "receipts"


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@materials_bp.route("/companies", methods=["GET", "POST"])
@login_required
def companies():
    if request.method == "POST":
        require_edit_access()
        company_name = request.form.get("company_name", "").strip()

        if not company_name:
            flash("Company name is required.", "danger")
            return redirect(url_for("materials.companies"))

        existing = Company.query.filter_by(name=company_name).first()
        if existing:
            if existing.scope == Company.SCOPE_MATERIALS:
                flash("This company already exists in Materials.", "warning")
            else:
                flash(
                    "This company name is already used in Ink Stock. Choose a different name.",
                    "danger",
                )
            return redirect(url_for("materials.companies"))

        company = Company(name=company_name, scope=Company.SCOPE_MATERIALS)
        db.session.add(company)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "Company",
            company.id,
            f"Materials company added: {company_name}",
        )
        db.session.commit()
        flash(f"Company '{company_name}' added.", "success")
        return redirect(url_for("materials.companies"))

    material_companies = get_material_companies()
    return render_template("materials/companies.html", companies=material_companies)


@materials_bp.route("/catalog", methods=["GET", "POST"])
@login_required
def catalog():
    companies = get_material_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        category = request.form.get("category", "PET").strip().upper()
        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()

        if not company_id or not material_name or category not in ("PET", "METALIZE", "LD"):
            flash("Company, category (PET/METALIZE/LD), and item name are required.", "danger")
            return redirect(url_for("materials.catalog"))

        try:
            material = create_material(
                company_id, material_name, size, category=category, micron=micron
            )
            log_audit(
                current_user.id,
                "CREATE",
                "Material",
                material.id,
                f"Material added: {material.display_name}",
            )
            db.session.commit()
            flash(f"Material '{material.display_name}' added.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        return redirect(url_for("materials.catalog"))

    materials = (
        Material.query.join(Company)
        .order_by(Company.name, Material.name, Material.size)
        .all()
    )
    return render_template(
        "materials/catalog.html",
        companies=companies,
        materials=materials,
        categories=("PET", "METALIZE", "LD"),
    )


@materials_bp.route("/opening-stock", methods=["GET", "POST"])
@login_required
def opening_stock():
    if request.method == "POST":
        require_edit_access()
        material_name = request.form.get("material_name", "").strip()
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if not material_name or quantity is None or not as_of_date:
            flash("Material, quantity (kg), and date are required.", "danger")
            return redirect(url_for("materials.opening_stock"))

        parsed_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        existing = MaterialOpeningStock.query.filter(
            db.func.lower(MaterialOpeningStock.material_name) == material_name.lower()
        ).first()

        if existing:
            existing.material_name = material_name
            existing.quantity = quantity
            existing.as_of_date = parsed_date
            existing.notes = notes
            existing.created_by_id = current_user.id
            action = "UPDATE"
            entity_id = existing.id
        else:
            record = MaterialOpeningStock(
                material_name=material_name,
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
            "MaterialOpeningStock",
            entity_id,
            f"{material_name}: opening stock set to {quantity} kg",
        )
        db.session.commit()
        flash("Opening stock saved successfully.", "success")
        return redirect(url_for("materials.opening_stock"))

    records = MaterialOpeningStock.query.order_by(MaterialOpeningStock.material_name).all()
    return render_template(
        "materials/opening_stock.html",
        records=records,
    )


@materials_bp.route("/receive", methods=["GET", "POST"])
@login_required
def receive_stock():
    companies = get_material_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        material_ref = request.form.get("material_id", "").strip()
        quantity = request.form.get("quantity", type=float)
        weights = parse_manual_weights(request.form)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not company_id
            or not material_ref
            or not quantity
            or quantity <= 0
            or not transaction_date
        ):
            flash("Company, material, quantity, and date are required.", "danger")
            return redirect(url_for("materials.receive_stock"))

        material = resolve_material_selection(company_id, material_ref)
        if not material:
            flash("Invalid material selection.", "danger")
            return redirect(url_for("materials.receive_stock"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = MaterialTransaction(
            company_id=company_id,
            material_id=material.id,
            transaction_type=MaterialTransaction.TRANSACTION_RECEIVED,
            quantity=quantity,
            weight_per_quantity=weights["weight_per_quantity"],
            gross_weight=weights["gross_weight"],
            tw=weights["tw"],
            net_weight=weights["net_weight"],
            micron=material.micron,
            transaction_date=parsed_date,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(txn)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "MaterialTransaction",
            txn.id,
            f"Received {quantity} kg of {material.display_name} for {txn.company.name}",
        )
        db.session.commit()
        flash(f"Stock received: {quantity} kg of '{material.display_name}' recorded.", "success")
        return redirect(url_for("materials.receive_stock"))

    recent_received = (
        MaterialTransaction.query.filter_by(
            transaction_type=MaterialTransaction.TRANSACTION_RECEIVED
        )
        .order_by(
            MaterialTransaction.transaction_date.desc(),
            MaterialTransaction.id.desc(),
        )
        .limit(30)
        .all()
    )
    return render_template(
        "materials/receive_stock.html",
        companies=companies,
        recent_received=recent_received,
    )


@materials_bp.route("/use", methods=["GET", "POST"])
@login_required
def use_stock():
    companies = get_material_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        material_ref = request.form.get("material_id", "").strip()
        quantity_left = request.form.get("quantity_left", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not company_id
            or not material_ref
            or quantity_left is None
            or quantity_left < 0
            or not transaction_date
        ):
            flash("Company, material, quantity left (kg), and date are required.", "danger")
            return redirect(url_for("materials.use_stock"))

        material = resolve_material_selection(company_id, material_ref)
        if not material or not material_matches_opening_stock(material):
            flash("Invalid material selection. Only opening stock materials can be used here.", "danger")
            return redirect(url_for("materials.use_stock"))

        material_id = material.id

        try:
            quantity_used = calculate_used_from_left(company_id, material_id, quantity_left)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("materials.use_stock"))

        if quantity_used <= 0:
            flash(
                "No stock was used — quantity left matches current stock. Nothing recorded.",
                "info",
            )
            return redirect(url_for("materials.use_stock"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = MaterialTransaction(
            company_id=company_id,
            material_id=material_id,
            transaction_type=MaterialTransaction.TRANSACTION_USED,
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
            "MaterialTransaction",
            txn.id,
            f"Used {quantity_used} kg of {material.display_name} ({quantity_left} kg left) for {txn.company.name}",
        )
        db.session.commit()
        flash(
            f"Daily usage recorded: {quantity_used:.1f} kg used, {quantity_left:.1f} kg left for '{material.display_name}'.",
            "success",
        )
        return redirect(url_for("materials.use_stock"))

    recent_usage = get_stock_usage_records()
    return render_template(
        "materials/use_stock.html",
        companies=companies,
        recent_usage=recent_usage,
    )


@materials_bp.route("/api/materials/<int:company_id>")
@login_required
def get_company_materials(company_id):
    context = request.args.get("context", "receive")
    if context not in {"receive", "use"}:
        return jsonify({"error": "Invalid context"}), 400
    return jsonify(get_company_material_options(company_id, context=context))


@materials_bp.route("/api/stock/<int:company_id>/<int:material_id>")
@login_required
def get_material_stock(company_id, material_id):
    material = Material.query.filter_by(id=material_id, company_id=company_id).first()
    if not material:
        return jsonify({"error": "Material not found"}), 404

    current = get_current_stock(company_id, material_id)
    return jsonify({"current_stock": current, "material_name": material.display_name})


@materials_bp.route("/live")
@login_required
def live_inventory():
    company_id = request.args.get("company_id", type=int)
    material_search = request.args.get("material", "").strip().lower()

    rows = calculate_live_stock(company_id=company_id)
    if material_search:
        rows = [
            r
            for r in rows
            if material_search in r["material"].display_name.lower()
        ]

    companies = get_material_companies()
    return render_template(
        "materials/live_inventory.html",
        rows=rows,
        companies=companies,
        selected_company=company_id,
        material_search=request.args.get("material", ""),
    )


@materials_bp.route("/purchase-receipts/<int:record_id>/file")
@login_required
def view_purchase_receipt(record_id):
    record = StockPurchaseReceipt.query.filter_by(
        id=record_id, module=StockPurchaseReceipt.MODULE_MATERIALS
    ).first_or_404()

    def backfill(rec, data, mimetype):
        rec.screenshot_data = data
        rec.screenshot_mimetype = mimetype
        db.session.commit()

    return resolve_receipt_file(record, backfill=backfill)


@materials_bp.route("/purchase-receipts", methods=["GET", "POST"])
@login_required
def purchase_receipts():
    companies = get_material_companies()

    if request.method == "POST":
        require_edit_access()
        receipt_date = request.form.get("receipt_date")
        company_id = request.form.get("company_id", type=int)
        transaction_id = request.form.get("material_transaction_id", type=int) or None
        title = request.form.get("title", "").strip()
        amount = request.form.get("amount", type=float)
        notes = request.form.get("notes", "").strip()
        screenshot = request.files.get("screenshot")

        if not receipt_date or not company_id:
            flash("Receipt date and company are required.", "danger")
            return redirect(url_for("materials.purchase_receipts"))

        try:
            prepared = save_receipt_upload(screenshot, MATERIALS_RECEIPT_DIR)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("materials.purchase_receipts"))

        record = StockPurchaseReceipt(
            module=StockPurchaseReceipt.MODULE_MATERIALS,
            receipt_date=datetime.strptime(receipt_date, "%Y-%m-%d").date(),
            company_id=company_id,
            material_transaction_id=transaction_id,
            title=title or None,
            amount=amount,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        apply_receipt_file(record, prepared)
        db.session.add(record)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "StockPurchaseReceipt",
            record.id,
            f"Materials purchase receipt for company #{company_id}",
        )
        db.session.commit()
        flash("Purchase receipt uploaded.", "success")
        return redirect(url_for("materials.purchase_receipts"))

    records = (
        StockPurchaseReceipt.query.filter_by(module=StockPurchaseReceipt.MODULE_MATERIALS)
        .order_by(StockPurchaseReceipt.receipt_date.desc(), StockPurchaseReceipt.id.desc())
        .all()
    )
    received_txns = (
        MaterialTransaction.query.filter_by(
            transaction_type=MaterialTransaction.TRANSACTION_RECEIVED
        )
        .order_by(MaterialTransaction.transaction_date.desc(), MaterialTransaction.id.desc())
        .limit(100)
        .all()
    )
    return render_template(
        "materials/purchase_receipts.html",
        records=records,
        companies=companies,
        received_txns=received_txns,
    )
