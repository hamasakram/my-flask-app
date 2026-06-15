from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (
    ChemicalItem,
    ChemicalOpeningStock,
    ChemicalTransaction,
    Company,
    GlueItem,
    GlueOpeningStock,
    GlueTransaction,
    Material,
    MaterialOpeningStock,
    MaterialTransaction,
    OpeningStock,
    InventoryTransaction,
    ShClientCompany,
    ShGatePass,
    ShLedgerEntry,
    ShOpeningBalance,
    ShPaymentScreenshot,
    ShPurchase,
    ShSupplierCompany,
)
from app.services.companies import (
    get_chemical_companies,
    get_glue_companies,
    get_ink_companies,
    get_material_companies,
)
from app.services.inventory import get_or_create_ink_type, log_audit
from app.services.sh_traders import calculate_gate_pass_total, calculate_total_amount
from app.services.sh_uploads import delete_payment_screenshot, save_payment_screenshot
from app.services.weights import parse_manual_weights

stock_edits_bp = Blueprint("stock_edits", __name__, url_prefix="/stock-edit")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


# --- Ink ---


@stock_edits_bp.route("/ink/received/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_ink_received(txn_id):
    txn = InventoryTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != InventoryTransaction.TRANSACTION_RECEIVED:
        abort(404)

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
            return redirect(url_for("stock_edits.edit_ink_received", txn_id=txn_id))

        try:
            ink = get_or_create_ink_type(
                company_id, ink_name, color_code=color_code, unit_type=unit_type
            )
            weights = parse_manual_weights(request.form)
            txn.company_id = company_id
            txn.ink_type_id = ink.id
            txn.quantity = quantity
            txn.weight_per_quantity = weights["weight_per_quantity"]
            txn.gross_weight = weights["gross_weight"]
            txn.tw = weights["tw"]
            txn.net_weight = weights["net_weight"]
            txn.transaction_date = _parse_date(transaction_date)
            txn.notes = notes
            log_audit(
                current_user.id,
                "UPDATE",
                "InventoryTransaction",
                txn.id,
                f"Updated received record: {quantity} of {ink.name}",
            )
            db.session.commit()
            flash("Stock received record updated.", "success")
            return redirect(url_for("inventory.receive_stock"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    return render_template(
        "shared/edit_ink_received.html",
        txn=txn,
        companies=get_ink_companies(),
        unit_types=("Can", "Drum", "Tin"),
        cancel_url=url_for("inventory.receive_stock"),
    )


@stock_edits_bp.route("/ink/used/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_ink_used(txn_id):
    txn = InventoryTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != InventoryTransaction.TRANSACTION_USED:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        quantity_left = request.form.get("quantity_left", type=float)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if quantity_left is None or quantity_left < 0 or not quantity or quantity <= 0 or not transaction_date:
            flash("Quantity left, used amount, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_ink_used", txn_id=txn_id))

        txn.quantity_left = quantity_left
        txn.quantity = quantity
        txn.transaction_date = _parse_date(transaction_date)
        txn.notes = notes
        log_audit(
            current_user.id,
            "UPDATE",
            "InventoryTransaction",
            txn.id,
            f"Updated usage record: {quantity} used, {quantity_left} left",
        )
        db.session.commit()
        flash("Stock used record updated.", "success")
        return redirect(url_for("inventory.use_stock"))

    return render_template(
        "shared/edit_ink_used.html",
        txn=txn,
        cancel_url=url_for("inventory.use_stock"),
    )


@stock_edits_bp.route("/ink/opening/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_ink_opening(record_id):
    record = OpeningStock.query.get_or_404(record_id)

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
            return redirect(url_for("stock_edits.edit_ink_opening", record_id=record_id))

        try:
            ink = get_or_create_ink_type(
                company_id, ink_name, color_code=color_code, unit_type=unit_type
            )
            record.company_id = company_id
            record.ink_type_id = ink.id
            record.quantity = quantity
            record.as_of_date = _parse_date(as_of_date)
            record.notes = notes
            record.created_by_id = current_user.id
            log_audit(
                current_user.id,
                "UPDATE",
                "OpeningStock",
                record.id,
                f"Updated opening stock: {ink.name} = {quantity}",
            )
            db.session.commit()
            flash("Opening stock updated.", "success")
            return redirect(url_for("inventory.opening_stock"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    return render_template(
        "shared/edit_ink_opening.html",
        record=record,
        companies=get_ink_companies(),
        unit_types=("Can", "Drum", "Tin"),
        cancel_url=url_for("inventory.opening_stock"),
    )


# --- Materials ---


@stock_edits_bp.route("/materials/received/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_materials_received(txn_id):
    txn = MaterialTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != MaterialTransaction.TRANSACTION_RECEIVED:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        material_id = request.form.get("material_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not company_id
            or not material_id
            or not quantity
            or quantity <= 0
            or not transaction_date
        ):
            flash("All required fields must be filled.", "danger")
            return redirect(url_for("stock_edits.edit_materials_received", txn_id=txn_id))

        material = Material.query.filter_by(id=material_id, company_id=company_id).first()
        if not material:
            flash("Invalid material selection.", "danger")
            return redirect(url_for("stock_edits.edit_materials_received", txn_id=txn_id))

        weights = parse_manual_weights(request.form)
        txn.company_id = company_id
        txn.material_id = material_id
        txn.quantity = quantity
        txn.weight_per_quantity = weights["weight_per_quantity"]
        txn.gross_weight = weights["gross_weight"]
        txn.tw = weights["tw"]
        txn.net_weight = weights["net_weight"]
        txn.micron = material.micron
        txn.transaction_date = _parse_date(transaction_date)
        txn.notes = notes
        log_audit(
            current_user.id,
            "UPDATE",
            "MaterialTransaction",
            txn.id,
            f"Updated purchase: {quantity} of {material.display_name}",
        )
        db.session.commit()
        flash("Purchase record updated.", "success")
        return redirect(url_for("materials.receive_stock"))

    return render_template(
        "shared/edit_materials_received.html",
        txn=txn,
        companies=get_material_companies(),
        cancel_url=url_for("materials.receive_stock"),
    )


@stock_edits_bp.route("/materials/used/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_materials_used(txn_id):
    txn = MaterialTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != MaterialTransaction.TRANSACTION_USED:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        quantity_left = request.form.get("quantity_left", type=float)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if quantity_left is None or quantity_left < 0 or not quantity or quantity <= 0 or not transaction_date:
            flash("Quantity left, used amount, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_materials_used", txn_id=txn_id))

        txn.quantity_left = quantity_left
        txn.quantity = quantity
        txn.transaction_date = _parse_date(transaction_date)
        txn.notes = notes
        log_audit(
            current_user.id,
            "UPDATE",
            "MaterialTransaction",
            txn.id,
            f"Updated usage: {quantity} used, {quantity_left} left",
        )
        db.session.commit()
        flash("Usage record updated.", "success")
        return redirect(url_for("materials.use_stock"))

    return render_template(
        "shared/edit_materials_used.html",
        txn=txn,
        cancel_url=url_for("materials.use_stock"),
    )


@stock_edits_bp.route("/materials/opening/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_materials_opening(record_id):
    record = MaterialOpeningStock.query.get_or_404(record_id)

    if request.method == "POST":
        require_edit_access()
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if quantity is None or not as_of_date:
            flash("Quantity and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_materials_opening", record_id=record_id))

        record.quantity = quantity
        record.as_of_date = _parse_date(as_of_date)
        record.notes = notes
        record.created_by_id = current_user.id
        log_audit(
            current_user.id,
            "UPDATE",
            "MaterialOpeningStock",
            record.id,
            f"Updated opening stock: {record.material.display_name} = {quantity}",
        )
        db.session.commit()
        flash("Opening stock updated.", "success")
        return redirect(url_for("materials.opening_stock"))

    return render_template(
        "shared/edit_materials_opening.html",
        record=record,
        cancel_url=url_for("materials.opening_stock"),
    )


# --- Glue ---


@stock_edits_bp.route("/glue/received/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_glue_received(txn_id):
    txn = GlueTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != GlueTransaction.TRANSACTION_RECEIVED:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_id = request.form.get("item_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not item_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, item, quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_glue_received", txn_id=txn_id))

        item = GlueItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item selection.", "danger")
            return redirect(url_for("stock_edits.edit_glue_received", txn_id=txn_id))

        weights = parse_manual_weights(request.form)
        txn.company_id = company_id
        txn.item_id = item_id
        txn.quantity = quantity
        txn.weight_per_quantity = weights["weight_per_quantity"]
        txn.gross_weight = weights["gross_weight"]
        txn.tw = weights["tw"]
        txn.net_weight = weights["net_weight"]
        txn.transaction_date = _parse_date(transaction_date)
        txn.notes = notes
        log_audit(
            current_user.id,
            "UPDATE",
            "GlueTransaction",
            txn.id,
            f"Updated received: {quantity} of {item.display_name}",
        )
        db.session.commit()
        flash("Stock received record updated.", "success")
        return redirect(url_for("glue.receive_stock"))

    return render_template(
        "shared/edit_product_received.html",
        txn=txn,
        companies=get_glue_companies(),
        module="glue",
        item_label="Item",
        cancel_url=url_for("glue.receive_stock"),
    )


@stock_edits_bp.route("/glue/used/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_glue_used(txn_id):
    txn = GlueTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != GlueTransaction.TRANSACTION_USED:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        quantity_left = request.form.get("quantity_left", type=float)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if quantity_left is None or quantity_left < 0 or not quantity or quantity <= 0 or not transaction_date:
            flash("Quantity left, used amount, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_glue_used", txn_id=txn_id))

        txn.quantity_left = quantity_left
        txn.quantity = quantity
        txn.transaction_date = _parse_date(transaction_date)
        txn.notes = notes
        log_audit(
            current_user.id,
            "UPDATE",
            "GlueTransaction",
            txn.id,
            f"Updated usage: {quantity} used, {quantity_left} left",
        )
        db.session.commit()
        flash("Usage record updated.", "success")
        return redirect(url_for("glue.use_stock"))

    return render_template(
        "shared/edit_product_used.html",
        txn=txn,
        module="glue",
        cancel_url=url_for("glue.use_stock"),
    )


@stock_edits_bp.route("/glue/opening/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_glue_opening(record_id):
    record = GlueOpeningStock.query.get_or_404(record_id)

    if request.method == "POST":
        require_edit_access()
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if quantity is None or not as_of_date:
            flash("Quantity and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_glue_opening", record_id=record_id))

        record.quantity = quantity
        record.as_of_date = _parse_date(as_of_date)
        record.notes = notes
        record.created_by_id = current_user.id
        log_audit(
            current_user.id,
            "UPDATE",
            "GlueOpeningStock",
            record.id,
            f"Updated opening stock: {record.item.display_name} = {quantity}",
        )
        db.session.commit()
        flash("Opening stock updated.", "success")
        return redirect(url_for("glue.opening_stock"))

    return render_template(
        "shared/edit_product_opening.html",
        record=record,
        module="glue",
        cancel_url=url_for("glue.opening_stock"),
    )


# --- Chemicals ---


@stock_edits_bp.route("/chemicals/received/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_chemicals_received(txn_id):
    txn = ChemicalTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != ChemicalTransaction.TRANSACTION_RECEIVED:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_id = request.form.get("item_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not item_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, item, quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_received", txn_id=txn_id))

        item = ChemicalItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item selection.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_received", txn_id=txn_id))

        weights = parse_manual_weights(request.form)
        txn.company_id = company_id
        txn.item_id = item_id
        txn.quantity = quantity
        txn.weight_per_quantity = weights["weight_per_quantity"]
        txn.gross_weight = weights["gross_weight"]
        txn.tw = weights["tw"]
        txn.net_weight = weights["net_weight"]
        txn.transaction_date = _parse_date(transaction_date)
        txn.notes = notes
        log_audit(
            current_user.id,
            "UPDATE",
            "ChemicalTransaction",
            txn.id,
            f"Updated received: {quantity} of {item.display_name}",
        )
        db.session.commit()
        flash("Stock received record updated.", "success")
        return redirect(url_for("chemicals.receive_stock"))

    return render_template(
        "shared/edit_product_received.html",
        txn=txn,
        companies=get_chemical_companies(),
        module="chemicals",
        item_label="Item",
        cancel_url=url_for("chemicals.receive_stock"),
    )


@stock_edits_bp.route("/chemicals/used/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_chemicals_used(txn_id):
    txn = ChemicalTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != ChemicalTransaction.TRANSACTION_USED:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        quantity_left = request.form.get("quantity_left", type=float)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if quantity_left is None or quantity_left < 0 or not quantity or quantity <= 0 or not transaction_date:
            flash("Quantity left, used amount, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_used", txn_id=txn_id))

        txn.quantity_left = quantity_left
        txn.quantity = quantity
        txn.transaction_date = _parse_date(transaction_date)
        txn.notes = notes
        log_audit(
            current_user.id,
            "UPDATE",
            "ChemicalTransaction",
            txn.id,
            f"Updated usage: {quantity} used, {quantity_left} left",
        )
        db.session.commit()
        flash("Usage record updated.", "success")
        return redirect(url_for("chemicals.use_stock"))

    return render_template(
        "shared/edit_product_used.html",
        txn=txn,
        module="chemicals",
        cancel_url=url_for("chemicals.use_stock"),
    )


@stock_edits_bp.route("/chemicals/opening/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_chemicals_opening(record_id):
    record = ChemicalOpeningStock.query.get_or_404(record_id)

    if request.method == "POST":
        require_edit_access()
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if quantity is None or not as_of_date:
            flash("Quantity and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_opening", record_id=record_id))

        record.quantity = quantity
        record.as_of_date = _parse_date(as_of_date)
        record.notes = notes
        record.created_by_id = current_user.id
        log_audit(
            current_user.id,
            "UPDATE",
            "ChemicalOpeningStock",
            record.id,
            f"Updated opening stock: {record.item.display_name} = {quantity}",
        )
        db.session.commit()
        flash("Opening stock updated.", "success")
        return redirect(url_for("chemicals.opening_stock"))

    return render_template(
        "shared/edit_product_opening.html",
        record=record,
        module="chemicals",
        cancel_url=url_for("chemicals.opening_stock"),
    )


# --- Company & catalog edits ---


@stock_edits_bp.route("/materials/company/<int:company_id>", methods=["GET", "POST"])
@login_required
def edit_materials_company(company_id):
    company = Company.query.get_or_404(company_id)
    if company.scope != Company.SCOPE_MATERIALS:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(url_for("stock_edits.edit_materials_company", company_id=company_id))

        existing = Company.query.filter(Company.name == name, Company.id != company_id).first()
        if existing:
            flash("This company name is already in use.", "danger")
            return redirect(url_for("stock_edits.edit_materials_company", company_id=company_id))

        company.name = name
        log_audit(current_user.id, "UPDATE", "Company", company.id, f"Renamed materials company to {name}")
        db.session.commit()
        flash("Company updated.", "success")
        return redirect(url_for("materials.companies"))

    return render_template(
        "shared/edit_company.html",
        company=company,
        module_label="Materials",
        cancel_url=url_for("materials.companies"),
    )


@stock_edits_bp.route("/materials/catalog/<int:material_id>", methods=["GET", "POST"])
@login_required
def edit_materials_catalog(material_id):
    material = Material.query.get_or_404(material_id)

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        category = request.form.get("category", "PET").strip().upper()
        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()

        if not company_id or not material_name or category not in ("PET", "METALIZE", "LD"):
            flash("Company, category, and item name are required.", "danger")
            return redirect(url_for("stock_edits.edit_materials_catalog", material_id=material_id))

        material.company_id = company_id
        material.category = category
        material.name = material_name
        material.size = size
        material.micron = micron or None
        log_audit(
            current_user.id,
            "UPDATE",
            "Material",
            material.id,
            f"Updated material: {material.display_name}",
        )
        db.session.commit()
        flash("Material updated.", "success")
        return redirect(url_for("materials.catalog"))

    return render_template(
        "shared/edit_material_catalog.html",
        material=material,
        companies=get_material_companies(),
        categories=("PET", "METALIZE", "LD"),
        cancel_url=url_for("materials.catalog"),
    )


@stock_edits_bp.route("/glue/company/<int:company_id>", methods=["GET", "POST"])
@login_required
def edit_glue_company(company_id):
    company = Company.query.get_or_404(company_id)
    if company.scope != Company.SCOPE_GLUE:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(url_for("stock_edits.edit_glue_company", company_id=company_id))

        existing = Company.query.filter(Company.name == name, Company.id != company_id).first()
        if existing:
            flash("This company name is already in use.", "danger")
            return redirect(url_for("stock_edits.edit_glue_company", company_id=company_id))

        company.name = name
        log_audit(current_user.id, "UPDATE", "Company", company.id, f"Renamed glue company to {name}")
        db.session.commit()
        flash("Company updated.", "success")
        return redirect(url_for("glue.companies"))

    return render_template(
        "shared/edit_company.html",
        company=company,
        module_label="Glue",
        cancel_url=url_for("glue.companies"),
    )


@stock_edits_bp.route("/glue/catalog/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_glue_catalog(item_id):
    item = GlueItem.query.get_or_404(item_id)

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_name = request.form.get("item_name", "").strip()
        unit_type = request.form.get("unit_type", "Kg").strip() or "Kg"

        if not company_id or not item_name:
            flash("Company and item name are required.", "danger")
            return redirect(url_for("stock_edits.edit_glue_catalog", item_id=item_id))

        item.company_id = company_id
        item.name = item_name
        item.unit_type = unit_type
        log_audit(current_user.id, "UPDATE", "GlueItem", item.id, f"Updated item: {item.display_name}")
        db.session.commit()
        flash("Item updated.", "success")
        return redirect(url_for("glue.catalog"))

    return render_template(
        "shared/edit_product_catalog.html",
        item=item,
        companies=get_glue_companies(),
        module="glue",
        cancel_url=url_for("glue.catalog"),
    )


@stock_edits_bp.route("/chemicals/company/<int:company_id>", methods=["GET", "POST"])
@login_required
def edit_chemicals_company(company_id):
    company = Company.query.get_or_404(company_id)
    if company.scope != Company.SCOPE_CHEMICALS:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_company", company_id=company_id))

        existing = Company.query.filter(Company.name == name, Company.id != company_id).first()
        if existing:
            flash("This company name is already in use.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_company", company_id=company_id))

        company.name = name
        log_audit(
            current_user.id, "UPDATE", "Company", company.id, f"Renamed chemicals company to {name}"
        )
        db.session.commit()
        flash("Company updated.", "success")
        return redirect(url_for("chemicals.companies"))

    return render_template(
        "shared/edit_company.html",
        company=company,
        module_label="Chemicals",
        cancel_url=url_for("chemicals.companies"),
    )


@stock_edits_bp.route("/chemicals/catalog/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_chemicals_catalog(item_id):
    item = ChemicalItem.query.get_or_404(item_id)

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        item_name = request.form.get("item_name", "").strip()
        unit_type = request.form.get("unit_type", "Kg").strip() or "Kg"

        if not company_id or not item_name:
            flash("Company and item name are required.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_catalog", item_id=item_id))

        item.company_id = company_id
        item.name = item_name
        item.unit_type = unit_type
        log_audit(
            current_user.id, "UPDATE", "ChemicalItem", item.id, f"Updated item: {item.display_name}"
        )
        db.session.commit()
        flash("Item updated.", "success")
        return redirect(url_for("chemicals.catalog"))

    return render_template(
        "shared/edit_product_catalog.html",
        item=item,
        companies=get_chemical_companies(),
        module="chemicals",
        cancel_url=url_for("chemicals.catalog"),
    )


# --- SH Traders ---


@stock_edits_bp.route("/sh/supplier/<int:company_id>", methods=["GET", "POST"])
@login_required
def edit_sh_supplier(company_id):
    company = ShSupplierCompany.query.get_or_404(company_id)

    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(url_for("stock_edits.edit_sh_supplier", company_id=company_id))

        existing = ShSupplierCompany.query.filter(
            ShSupplierCompany.name == name, ShSupplierCompany.id != company_id
        ).first()
        if existing:
            flash("This supplier name is already in use.", "danger")
            return redirect(url_for("stock_edits.edit_sh_supplier", company_id=company_id))

        company.name = name
        log_audit(
            current_user.id, "UPDATE", "ShSupplierCompany", company.id, f"Renamed supplier to {name}"
        )
        db.session.commit()
        flash("Supplier updated.", "success")
        return redirect(url_for("sh_main.suppliers"))

    return render_template(
        "shared/edit_company.html",
        company=company,
        module_label="SH Traders (Supplier)",
        cancel_url=url_for("sh_main.suppliers"),
    )


@stock_edits_bp.route("/sh/client/<int:company_id>", methods=["GET", "POST"])
@login_required
def edit_sh_client(company_id):
    company = ShClientCompany.query.get_or_404(company_id)

    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(url_for("stock_edits.edit_sh_client", company_id=company_id))

        existing = ShClientCompany.query.filter(
            ShClientCompany.name == name, ShClientCompany.id != company_id
        ).first()
        if existing:
            flash("This client name is already in use.", "danger")
            return redirect(url_for("stock_edits.edit_sh_client", company_id=company_id))

        company.name = name
        log_audit(
            current_user.id, "UPDATE", "ShClientCompany", company.id, f"Renamed client to {name}"
        )
        db.session.commit()
        flash("Client updated.", "success")
        return redirect(url_for("sh_main.clients"))

    return render_template(
        "shared/edit_company.html",
        company=company,
        module_label="SH Traders (Client)",
        cancel_url=url_for("sh_main.clients"),
    )


@stock_edits_bp.route("/sh/purchase/<int:purchase_id>", methods=["GET", "POST"])
@login_required
def edit_sh_purchase(purchase_id):
    purchase = ShPurchase.query.get_or_404(purchase_id)
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        date_purchased = request.form.get("date_purchased")
        supplier_id = request.form.get("supplier_company_id", type=int)
        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()
        total_kg = request.form.get("total_kg", type=float)
        rate_per_1000 = request.form.get("rate_per_1000_kg", type=float)
        paid_amount = request.form.get("paid_amount", type=float) or 0
        client_id = request.form.get("client_company_id", type=int)
        notes = request.form.get("notes", "").strip()

        if (
            not date_purchased
            or not supplier_id
            or not material_name
            or not total_kg
            or total_kg <= 0
            or not rate_per_1000
            or rate_per_1000 <= 0
            or not client_id
        ):
            flash("All required fields must be filled.", "danger")
            return redirect(url_for("stock_edits.edit_sh_purchase", purchase_id=purchase_id))

        purchase.date_purchased = _parse_date(date_purchased)
        purchase.supplier_company_id = supplier_id
        purchase.material_name = material_name
        purchase.size = size
        purchase.micron = micron or None
        purchase.total_kg = total_kg
        purchase.rate_per_1000_kg = rate_per_1000
        purchase.total_amount = calculate_total_amount(total_kg, rate_per_1000)
        purchase.paid_amount = paid_amount
        purchase.client_company_id = client_id
        purchase.notes = notes or None
        log_audit(
            current_user.id,
            "UPDATE",
            "ShPurchase",
            purchase.id,
            f"Updated SH purchase #{purchase_id}",
        )
        db.session.commit()
        flash("Purchase updated.", "success")
        return redirect(url_for("sh_main.purchases"))

    return render_template(
        "sh_traders/edit_purchase.html",
        purchase=purchase,
        suppliers=suppliers,
        clients=clients,
        cancel_url=url_for("sh_main.purchases"),
    )


@stock_edits_bp.route("/sh/opening", methods=["GET", "POST"])
@login_required
def edit_sh_opening():
    opening = ShOpeningBalance.query.order_by(ShOpeningBalance.id.asc()).first()
    if not opening:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        amount = request.form.get("opening_amount", type=float)
        notes = request.form.get("opening_notes", "").strip()
        if amount is None or amount < 0:
            flash("Enter a valid opening balance.", "danger")
            return redirect(url_for("stock_edits.edit_sh_opening"))

        opening.amount = amount
        opening.notes = notes or None
        log_audit(
            current_user.id,
            "UPDATE",
            "ShOpeningBalance",
            opening.id,
            f"Updated opening balance to {amount:,.2f}",
        )
        db.session.commit()
        flash("Opening balance updated.", "success")
        return redirect(url_for("sh_main.payments"))

    return render_template(
        "sh_traders/edit_opening.html",
        opening=opening,
        cancel_url=url_for("sh_main.payments"),
    )


@stock_edits_bp.route("/sh/ledger/<int:entry_id>", methods=["GET", "POST"])
@login_required
def edit_sh_ledger(entry_id):
    entry = ShLedgerEntry.query.get_or_404(entry_id)

    if request.method == "POST":
        require_edit_access()
        entry_date = request.form.get("entry_date")
        debit = request.form.get("debit", type=float) or 0
        credit = request.form.get("credit", type=float) or 0
        supplier_id = request.form.get("supplier_company_id", type=int) or None
        client_id = request.form.get("client_company_id", type=int) or None
        notes = request.form.get("notes", "").strip()

        if not entry_date:
            flash("Entry date is required.", "danger")
            return redirect(url_for("stock_edits.edit_sh_ledger", entry_id=entry_id))

        if debit <= 0 and credit <= 0:
            flash("Enter a debit or credit amount.", "danger")
            return redirect(url_for("stock_edits.edit_sh_ledger", entry_id=entry_id))

        if debit > 0 and credit > 0:
            flash("Enter either debit or credit, not both.", "danger")
            return redirect(url_for("stock_edits.edit_sh_ledger", entry_id=entry_id))

        entry.entry_date = _parse_date(entry_date)
        entry.debit = debit
        entry.credit = credit
        entry.supplier_company_id = supplier_id
        entry.client_company_id = client_id
        entry.notes = notes or None
        log_audit(
            current_user.id,
            "UPDATE",
            "ShLedgerEntry",
            entry.id,
            f"Updated ledger entry #{entry_id}",
        )
        db.session.commit()
        flash("Ledger entry updated.", "success")
        return redirect(url_for("sh_main.payments"))

    return render_template(
        "sh_traders/edit_ledger.html",
        entry=entry,
        suppliers=ShSupplierCompany.query.order_by(ShSupplierCompany.name).all(),
        clients=ShClientCompany.query.order_by(ShClientCompany.name).all(),
        cancel_url=url_for("sh_main.payments"),
    )


@stock_edits_bp.route("/sh/payment-screenshot/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_sh_payment_screenshot(record_id):
    record = ShPaymentScreenshot.query.get_or_404(record_id)
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    purchases = ShPurchase.query.order_by(ShPurchase.date_purchased.desc()).all()

    if request.method == "POST":
        require_edit_access()
        payment_date = request.form.get("payment_date")
        supplier_id = request.form.get("supplier_company_id", type=int)
        amount_paid = request.form.get("amount_paid", type=float)
        purchase_id = request.form.get("purchase_id", type=int) or None
        notes = request.form.get("notes", "").strip()
        screenshot = request.files.get("screenshot")

        if not payment_date or not supplier_id:
            flash("Payment date and supplier are required.", "danger")
            return redirect(url_for("stock_edits.edit_sh_payment_screenshot", record_id=record_id))

        record.payment_date = _parse_date(payment_date)
        record.supplier_company_id = supplier_id
        record.amount_paid = amount_paid
        record.purchase_id = purchase_id
        record.notes = notes or None

        if screenshot and screenshot.filename:
            try:
                new_filename = save_payment_screenshot(screenshot)
                delete_payment_screenshot(record.screenshot_filename)
                record.screenshot_filename = new_filename
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("stock_edits.edit_sh_payment_screenshot", record_id=record_id))

        log_audit(
            current_user.id,
            "UPDATE",
            "ShPaymentScreenshot",
            record.id,
            f"Updated payment screenshot #{record_id}",
        )
        db.session.commit()
        flash("Payment screenshot updated.", "success")
        return redirect(url_for("sh_main.payment_screenshots"))

    return render_template(
        "sh_traders/edit_payment_screenshot.html",
        record=record,
        suppliers=suppliers,
        purchases=purchases,
        cancel_url=url_for("sh_main.payment_screenshots"),
    )


@stock_edits_bp.route("/sh/gate-pass/<int:gate_pass_id>", methods=["GET", "POST"])
@login_required
def edit_sh_gate_pass(gate_pass_id):
    gate_pass = ShGatePass.query.get_or_404(gate_pass_id)
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        issued_date = request.form.get("issued_date")
        issued_time = request.form.get("issued_time")
        sold_to_id = request.form.get("sold_to_client_id", type=int)
        supplier_id = request.form.get("supplier_company_id", type=int)
        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()
        rolls = request.form.get("rolls", type=float)
        gross_weight_per_roll = request.form.get("gross_weight_per_roll", type=float)
        net_weight_per_roll = request.form.get("net_weight_per_roll", type=float)
        gross_weight = request.form.get("gross_weight", type=float)
        net_weight = request.form.get("net_weight", type=float)
        amount_per_kg = request.form.get("amount_per_kg", type=float)
        notes = request.form.get("notes", "").strip()

        if (
            not issued_date
            or not issued_time
            or not sold_to_id
            or not supplier_id
            or not material_name
            or not gross_weight
            or gross_weight <= 0
            or not net_weight
            or net_weight <= 0
            or not amount_per_kg
            or amount_per_kg <= 0
        ):
            flash("All required fields must be filled.", "danger")
            return redirect(url_for("stock_edits.edit_sh_gate_pass", gate_pass_id=gate_pass_id))

        gate_pass.issued_at = datetime.strptime(f"{issued_date} {issued_time}", "%Y-%m-%d %H:%M")
        gate_pass.sold_to_client_id = sold_to_id
        gate_pass.supplier_company_id = supplier_id
        gate_pass.material_name = material_name
        gate_pass.size = size
        gate_pass.micron = micron or None
        gate_pass.rolls = rolls
        gate_pass.gross_weight_per_roll = gross_weight_per_roll
        gate_pass.net_weight_per_roll = net_weight_per_roll
        gate_pass.gross_weight = gross_weight
        gate_pass.net_weight = net_weight
        gate_pass.amount_per_kg = amount_per_kg
        gate_pass.total_amount = calculate_gate_pass_total(net_weight, amount_per_kg)
        gate_pass.notes = notes or None
        log_audit(
            current_user.id,
            "UPDATE",
            "ShGatePass",
            gate_pass.id,
            f"Updated gate pass {gate_pass.gate_pass_number}",
        )
        db.session.commit()
        flash("Gate pass updated.", "success")
        return redirect(url_for("sh_main.gate_passes"))

    return render_template(
        "sh_traders/edit_gate_pass.html",
        gate_pass=gate_pass,
        suppliers=suppliers,
        clients=clients,
        cancel_url=url_for("sh_main.gate_passes"),
    )
