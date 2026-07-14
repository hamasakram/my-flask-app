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
    BankAccount,
    BankLedgerEntry,
    BankTransfer,
    HomeLedgerEntry,
    HomeParty,
    InkType,
    InventoryTransaction,
    StockPurchaseReceipt,
    ShClientCompany,
    ShSaleInvoice,
    ShLedgerEntry,
    ShOpeningBalance,
    ShGatePassScreenshot,
    ShPaymentScreenshot,
    ShPartnerCompany,
    ShPurchase,
    ShSupplierCompany,
)
from app.services.companies import (
    get_chemical_companies,
    get_glue_companies,
    get_ink_companies,
)
from app.services.bank_ledger import bank_account_exists, update_bank_transfer
from app.services.inventory import create_ink_type, log_audit
from app.services.receipt_uploads import apply_receipt_file, delete_receipt_file, save_receipt_upload
from app.services.sh_sale_invoice import compute_current_balance, parse_invoice_lines, save_invoice_lines
from app.services.sh_traders import calculate_total_amount
from app.services.sh_partnership import apply_partnership_from_form
from app.services.sh_partnership import apply_partnership_from_form
from app.services.sh_uploads import apply_gate_pass_screenshot, apply_payment_screenshot, delete_gate_pass_screenshot, delete_payment_screenshot, save_gate_pass_screenshot, save_payment_screenshot
from app.services.weights import parse_manual_weights
from app.services.materials_inventory import (
    get_materials_in_opening_stock,
    is_valid_opening_stock_selection,
    sync_opening_stock_material,
)

from pathlib import Path

stock_edits_bp = Blueprint("stock_edits", __name__, url_prefix="/stock-edit")
INK_RECEIPT_DIR = Path("uploads") / "ink" / "receipts"
MATERIALS_RECEIPT_DIR = Path("uploads") / "materials" / "receipts"


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
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_type_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, ink, valid quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_ink_received", txn_id=txn_id))

        ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
        if not ink:
            flash("Select a valid ink for this company.", "danger")
            return redirect(url_for("stock_edits.edit_ink_received", txn_id=txn_id))

        try:
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


@stock_edits_bp.route("/ink/issued/<int:txn_id>", methods=["GET", "POST"])
@login_required
def edit_ink_issued(txn_id):
    from app.services.inventory import get_stored_stock

    txn = InventoryTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != InventoryTransaction.TRANSACTION_ISSUED:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_type_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, ink, valid quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_ink_issued", txn_id=txn_id))

        ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
        if not ink:
            flash("Select a valid ink for this company.", "danger")
            return redirect(url_for("stock_edits.edit_ink_issued", txn_id=txn_id))

        if company_id == txn.company_id and ink_type_id == txn.ink_type_id:
            available = get_stored_stock(company_id, ink_type_id) + txn.quantity
        else:
            available = get_stored_stock(company_id, ink_type_id)

        if quantity > available:
            flash(
                f"Cannot issue {quantity:.1f} — only {available:.1f} available in stored backup.",
                "danger",
            )
            return redirect(url_for("stock_edits.edit_ink_issued", txn_id=txn_id))

        txn.company_id = company_id
        txn.ink_type_id = ink.id
        txn.quantity = quantity
        txn.transaction_date = _parse_date(transaction_date)
        txn.notes = notes
        log_audit(
            current_user.id,
            "UPDATE",
            "InventoryTransaction",
            txn.id,
            f"Updated issue record: {quantity} of {ink.name}",
        )
        db.session.commit()
        flash("Issue record updated.", "success")
        return redirect(url_for("inventory.issue_to_use"))

    return render_template(
        "shared/edit_ink_issued.html",
        txn=txn,
        companies=get_ink_companies(),
        cancel_url=url_for("inventory.issue_to_use"),
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
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_type_id or quantity is None or not as_of_date:
            flash("Company, ink, quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_ink_opening", record_id=record_id))

        ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
        if not ink:
            flash("Select a valid ink for this company.", "danger")
            return redirect(url_for("stock_edits.edit_ink_opening", record_id=record_id))

        try:
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
        material_id = request.form.get("material_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not material_id
            or not quantity
            or quantity <= 0
            or not transaction_date
        ):
            flash("All required fields must be filled.", "danger")
            return redirect(url_for("stock_edits.edit_materials_received", txn_id=txn_id))

        material = Material.query.filter_by(id=material_id).first()
        if not material or not is_valid_opening_stock_selection(str(material_id)):
            flash("Invalid material selection. Only opening stock materials are allowed.", "danger")
            return redirect(url_for("stock_edits.edit_materials_received", txn_id=txn_id))

        weights = parse_manual_weights(request.form)
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

    materials = get_materials_in_opening_stock()
    return render_template(
        "shared/edit_materials_received.html",
        txn=txn,
        materials=materials,
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
        material_name = request.form.get("material_name", "").strip()
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if not material_name or quantity is None or not as_of_date:
            flash("Material, quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_materials_opening", record_id=record_id))

        duplicate = MaterialOpeningStock.query.filter(
            db.func.lower(MaterialOpeningStock.material_name) == material_name.lower(),
            MaterialOpeningStock.id != record.id,
        ).first()
        if duplicate:
            flash("Opening stock for this material already exists.", "danger")
            return redirect(url_for("stock_edits.edit_materials_opening", record_id=record_id))

        record.material_name = material_name
        record.quantity = quantity
        record.as_of_date = _parse_date(as_of_date)
        record.notes = notes
        record.created_by_id = current_user.id
        log_audit(
            current_user.id,
            "UPDATE",
            "MaterialOpeningStock",
            record.id,
            f"Updated opening stock: {material_name} = {quantity}",
        )
        sync_opening_stock_material(material_name)
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
    flash("Companies are not used in Materials.", "info")
    return redirect(url_for("materials.catalog"))


@stock_edits_bp.route("/ink/company/<int:company_id>", methods=["GET", "POST"])
@login_required
def edit_ink_company(company_id):
    company = Company.query.get_or_404(company_id)
    if company.scope != Company.SCOPE_INK:
        abort(404)

    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(url_for("stock_edits.edit_ink_company", company_id=company_id))

        existing = Company.query.filter(Company.name == name, Company.id != company_id).first()
        if existing:
            flash("This company name is already in use.", "danger")
            return redirect(url_for("stock_edits.edit_ink_company", company_id=company_id))

        company.name = name
        log_audit(current_user.id, "UPDATE", "Company", company.id, f"Renamed ink company to {name}")
        db.session.commit()
        flash("Company updated.", "success")
        return redirect(url_for("inventory.companies"))

    return render_template(
        "shared/edit_company.html",
        company=company,
        module_label="Ink Stock",
        cancel_url=url_for("inventory.companies"),
    )


@stock_edits_bp.route("/ink/catalog/<int:ink_id>", methods=["GET", "POST"])
@login_required
def edit_ink_catalog(ink_id):
    ink = InkType.query.get_or_404(ink_id)

    if request.method == "POST":
        require_edit_access()
        ink_name = request.form.get("ink_name", "").strip()
        color_code = request.form.get("color_code", "").strip()
        unit_type = request.form.get("unit_type", "").strip()

        if not ink_name:
            flash("Ink name is required.", "danger")
            return redirect(url_for("stock_edits.edit_ink_catalog", ink_id=ink_id))

        existing = InkType.query.filter(
            InkType.company_id == ink.company_id,
            InkType.name == ink_name,
            InkType.id != ink_id,
        ).first()
        if existing:
            flash("This ink name already exists for this company.", "danger")
            return redirect(url_for("stock_edits.edit_ink_catalog", ink_id=ink_id))

        ink.name = ink_name
        ink.color_code = color_code or None
        ink.unit_type = unit_type or None
        log_audit(current_user.id, "UPDATE", "InkType", ink.id, f"Updated ink: {ink_name}")
        db.session.commit()
        flash("Ink updated.", "success")
        return redirect(url_for("inventory.catalog"))

    return render_template(
        "shared/edit_ink_catalog.html",
        ink=ink,
        unit_types=("Can", "Drum", "Tin"),
        cancel_url=url_for("inventory.catalog"),
    )


@stock_edits_bp.route("/materials/catalog/<int:material_id>", methods=["GET", "POST"])
@login_required
def edit_materials_catalog(material_id):
    material = Material.query.get_or_404(material_id)

    if request.method == "POST":
        require_edit_access()
        category = request.form.get("category", "PET").strip().upper()
        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()

        if not material_name or category not in ("PET", "METALIZE", "LD"):
            flash("Category and item name are required.", "danger")
            return redirect(url_for("stock_edits.edit_materials_catalog", material_id=material_id))

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
        client_rate = request.form.get("client_rate_per_kg", type=float) or 0
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
        purchase.client_rate_per_kg = client_rate if client_rate > 0 else None
        purchase.client_total_amount = (
            calculate_total_amount(total_kg, client_rate) if client_rate > 0 else None
        )
        purchase.paid_amount = paid_amount
        purchase.client_company_id = client_id
        purchase.notes = notes or None
        try:
            apply_partnership_from_form(purchase, request.form)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("stock_edits.edit_sh_purchase", purchase_id=purchase_id))
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

    partners = ShPartnerCompany.query.order_by(ShPartnerCompany.name).all()
    existing_shares = purchase.partner_shares.all() if purchase.has_partnership else []
    return render_template(
        "sh_traders/edit_purchase.html",
        purchase=purchase,
        suppliers=suppliers,
        clients=clients,
        partners=partners,
        existing_shares=existing_shares,
        cancel_url=url_for("sh_main.purchases"),
    )


@stock_edits_bp.route("/sh/partner/<int:company_id>", methods=["GET", "POST"])
@login_required
def edit_sh_partner(company_id):
    company = ShPartnerCompany.query.get_or_404(company_id)

    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Partner name is required.", "danger")
            return redirect(url_for("stock_edits.edit_sh_partner", company_id=company_id))

        existing = ShPartnerCompany.query.filter(
            ShPartnerCompany.name == name, ShPartnerCompany.id != company_id
        ).first()
        if existing:
            flash("Another partner already uses this name.", "warning")
            return redirect(url_for("stock_edits.edit_sh_partner", company_id=company_id))

        company.name = name
        log_audit(
            current_user.id,
            "UPDATE",
            "ShPartnerCompany",
            company.id,
            f"Renamed partner to {name}",
        )
        db.session.commit()
        flash("Partner updated.", "success")
        return redirect(url_for("sh_main.partners"))

    return render_template(
        "shared/edit_company.html",
        company=company,
        module_label="SH Traders (Partner)",
        cancel_url=url_for("sh_main.partners"),
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
        partner_id = request.form.get("partner_company_id", type=int) or None
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
        entry.partner_company_id = partner_id
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
        partners=ShPartnerCompany.query.order_by(ShPartnerCompany.name).all(),
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
                prepared = save_payment_screenshot(screenshot)
                delete_payment_screenshot(record.screenshot_filename)
                apply_payment_screenshot(record, prepared)
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


@stock_edits_bp.route("/sh/gate-pass-screenshot/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_sh_gate_pass_screenshot(record_id):
    record = ShGatePassScreenshot.query.get_or_404(record_id)
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    invoices = ShSaleInvoice.query.order_by(ShSaleInvoice.invoice_date.desc()).all()

    if request.method == "POST":
        require_edit_access()
        gate_pass_date = request.form.get("gate_pass_date")
        sold_to_id = request.form.get("sold_to_client_id", type=int) or None
        sale_invoice_id = request.form.get("sale_invoice_id", type=int) or None
        title = request.form.get("title", "").strip()
        notes = request.form.get("notes", "").strip()
        screenshot = request.files.get("screenshot")

        if not gate_pass_date:
            flash("Gate pass date is required.", "danger")
            return redirect(url_for("stock_edits.edit_sh_gate_pass_screenshot", record_id=record_id))

        record.gate_pass_date = _parse_date(gate_pass_date)
        record.sold_to_client_id = sold_to_id
        record.sale_invoice_id = sale_invoice_id
        record.title = title or None
        record.notes = notes or None

        if screenshot and screenshot.filename:
            try:
                prepared = save_gate_pass_screenshot(screenshot)
                delete_gate_pass_screenshot(record.screenshot_filename)
                apply_gate_pass_screenshot(record, prepared)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("stock_edits.edit_sh_gate_pass_screenshot", record_id=record_id))

        log_audit(
            current_user.id,
            "UPDATE",
            "ShGatePassScreenshot",
            record.id,
            f"Updated gate pass screenshot #{record_id}",
        )
        db.session.commit()
        flash("Gate pass screenshot updated.", "success")
        return redirect(url_for("sh_main.gate_pass_screenshots"))

    return render_template(
        "sh_traders/edit_gate_pass_screenshot.html",
        record=record,
        clients=clients,
        invoices=invoices,
        cancel_url=url_for("sh_main.gate_pass_screenshots"),
    )


@stock_edits_bp.route("/sh/sale-invoice/<int:invoice_id>", methods=["GET", "POST"])
@login_required
def edit_sh_sale_invoice(invoice_id):
    invoice = ShSaleInvoice.query.get_or_404(invoice_id)
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        invoice_date = request.form.get("invoice_date")
        invoice_number = request.form.get("invoice_number", "").strip()
        factory_challan_no = request.form.get("factory_challan_no", "").strip()
        sold_to_id = request.form.get("sold_to_client_id", type=int)
        location = request.form.get("location", "MULTAN").strip() or "MULTAN"
        previous_balance = request.form.get("previous_balance", type=float) or 0.0
        previous_balance_type = request.form.get("previous_balance_type", "DR").strip() or "DR"
        current_balance_override = request.form.get("current_balance", type=float)
        current_balance_type = request.form.get("current_balance_type", "DR").strip() or "DR"
        notes = request.form.get("notes", "").strip()

        if not invoice_date or not sold_to_id or not invoice_number:
            flash("Invoice date, number, and sold to client are required.", "danger")
            return redirect(url_for("stock_edits.edit_sh_sale_invoice", invoice_id=invoice_id))

        try:
            invoice.invoice_date = datetime.strptime(invoice_date, "%Y-%m-%d").date()
            lines = parse_invoice_lines(request.form)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("stock_edits.edit_sh_sale_invoice", invoice_id=invoice_id))

        invoice.invoice_number = invoice_number
        invoice.factory_challan_no = factory_challan_no or None
        invoice.sold_to_client_id = sold_to_id
        invoice.location = location
        invoice.previous_balance = previous_balance
        invoice.previous_balance_type = previous_balance_type
        invoice.current_balance_type = current_balance_type
        invoice.notes = notes or None

        total_amount = save_invoice_lines(invoice, lines)
        invoice.total_amount = total_amount
        if current_balance_override is not None and not request.form.get("auto_current_balance"):
            invoice.current_balance = current_balance_override
        else:
            current, balance_type = compute_current_balance(
                previous_balance, total_amount, previous_balance_type
            )
            invoice.current_balance = current
            invoice.current_balance_type = balance_type

        log_audit(
            current_user.id,
            "UPDATE",
            "ShSaleInvoice",
            invoice.id,
            f"Updated sale invoice {invoice.invoice_number}",
        )
        db.session.commit()
        flash("Sale invoice updated.", "success")
        return redirect(url_for("sh_main.sale_invoices"))

    return render_template(
        "sh_traders/edit_sale_invoice.html",
        invoice=invoice,
        clients=clients,
        cancel_url=url_for("sh_main.sale_invoices"),
    )


# --- Home Ledger ---


@stock_edits_bp.route("/home/party/<int:party_id>", methods=["GET", "POST"])
@login_required
def edit_home_party(party_id):
    party = HomeParty.query.get_or_404(party_id)

    if request.method == "POST":
        require_edit_access()
        name = request.form.get("party_name", "").strip()
        balance_kind = request.form.get("balance_kind", HomeParty.KIND_TO_PAY)
        opening_amount = request.form.get("opening_amount", type=float) or 0
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Party name is required.", "danger")
            return redirect(url_for("stock_edits.edit_home_party", party_id=party_id))

        existing = HomeParty.query.filter(HomeParty.name == name, HomeParty.id != party_id).first()
        if existing:
            flash("This party name is already in use.", "danger")
            return redirect(url_for("stock_edits.edit_home_party", party_id=party_id))

        if balance_kind not in (HomeParty.KIND_TO_PAY, HomeParty.KIND_TO_RECEIVE):
            balance_kind = HomeParty.KIND_TO_PAY

        party.name = name
        party.balance_kind = balance_kind
        party.opening_amount = opening_amount
        party.notes = notes or None
        log_audit(current_user.id, "UPDATE", "HomeParty", party.id, f"Updated home party: {name}")
        db.session.commit()
        flash("Party updated.", "success")
        return redirect(url_for("home_ledger.party_ledger", party_id=party_id))

    return render_template(
        "home_ledger/edit_party.html",
        party=party,
        cancel_url=url_for("home_ledger.party_ledger", party_id=party_id),
    )


@stock_edits_bp.route("/home/ledger/<int:entry_id>", methods=["GET", "POST"])
@login_required
def edit_home_ledger_entry(entry_id):
    entry = HomeLedgerEntry.query.get_or_404(entry_id)

    if request.method == "POST":
        require_edit_access()
        entry_date = request.form.get("entry_date")
        given = request.form.get("given", type=float) or 0
        received = request.form.get("received", type=float) or 0
        notes = request.form.get("notes", "").strip()

        if not entry_date:
            flash("Entry date is required.", "danger")
            return redirect(url_for("stock_edits.edit_home_ledger_entry", entry_id=entry_id))

        if given <= 0 and received <= 0:
            flash("Enter a given or received amount.", "danger")
            return redirect(url_for("stock_edits.edit_home_ledger_entry", entry_id=entry_id))

        if given > 0 and received > 0:
            flash("Enter either given or received, not both.", "danger")
            return redirect(url_for("stock_edits.edit_home_ledger_entry", entry_id=entry_id))

        entry.entry_date = _parse_date(entry_date)
        entry.given = given
        entry.received = received
        entry.notes = notes or None
        log_audit(
            current_user.id,
            "UPDATE",
            "HomeLedgerEntry",
            entry.id,
            f"Updated home ledger entry #{entry_id}",
        )
        db.session.commit()
        flash("Ledger entry updated.", "success")
        return redirect(url_for("home_ledger.party_ledger", party_id=entry.party_id))

    return render_template(
        "home_ledger/edit_entry.html",
        entry=entry,
        cancel_url=url_for("home_ledger.party_ledger", party_id=entry.party_id),
    )


# --- Bank Ledger ---


@stock_edits_bp.route("/bank/account/<int:bank_id>", methods=["GET", "POST"])
@login_required
def edit_bank_account(bank_id):
    bank = BankAccount.query.get_or_404(bank_id)

    if request.method == "POST":
        require_edit_access()
        bank_name = request.form.get("bank_name", "").strip()
        account_title = request.form.get("account_title", "").strip()
        account_number = request.form.get("account_number", "").strip()
        branch = request.form.get("branch", "").strip()
        opening_balance = request.form.get("opening_balance", type=float) or 0
        notes = request.form.get("notes", "").strip()

        if not bank_name:
            flash("Bank name is required.", "danger")
            return redirect(url_for("stock_edits.edit_bank_account", bank_id=bank_id))

        if bank_account_exists(bank_name, account_number or None, exclude_id=bank_id):
            flash("This bank account already exists.", "danger")
            return redirect(url_for("stock_edits.edit_bank_account", bank_id=bank_id))

        bank.bank_name = bank_name
        bank.account_title = account_title or None
        bank.account_number = account_number or None
        bank.branch = branch or None
        bank.opening_balance = opening_balance
        bank.notes = notes or None
        log_audit(current_user.id, "UPDATE", "BankAccount", bank.id, f"Updated bank: {bank.display_name}")
        db.session.commit()
        flash("Bank account updated.", "success")
        return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank_id))

    return render_template(
        "bank_ledger/edit_bank.html",
        bank=bank,
        cancel_url=url_for("bank_ledger.bank_ledger", bank_id=bank_id),
    )


@stock_edits_bp.route("/bank/ledger/<int:entry_id>", methods=["GET", "POST"])
@login_required
def edit_bank_ledger_entry(entry_id):
    entry = BankLedgerEntry.query.get_or_404(entry_id)

    if entry.is_transfer:
        flash("Edit the full transfer to change both bank ledgers together.", "info")
        return redirect(url_for("stock_edits.edit_bank_transfer", transfer_id=entry.transfer_id))

    if request.method == "POST":
        require_edit_access()
        entry_date = request.form.get("entry_date")
        deposit = request.form.get("deposit", type=float) or 0
        withdrawal = request.form.get("withdrawal", type=float) or 0
        notes = request.form.get("notes", "").strip()

        if not entry_date:
            flash("Entry date is required.", "danger")
            return redirect(url_for("stock_edits.edit_bank_ledger_entry", entry_id=entry_id))

        if deposit <= 0 and withdrawal <= 0:
            flash("Enter a deposit or withdrawal amount.", "danger")
            return redirect(url_for("stock_edits.edit_bank_ledger_entry", entry_id=entry_id))

        if deposit > 0 and withdrawal > 0:
            flash("Enter either deposit or withdrawal, not both.", "danger")
            return redirect(url_for("stock_edits.edit_bank_ledger_entry", entry_id=entry_id))

        entry.entry_date = _parse_date(entry_date)
        entry.deposit = deposit
        entry.withdrawal = withdrawal
        entry.notes = notes or None
        log_audit(
            current_user.id,
            "UPDATE",
            "BankLedgerEntry",
            entry.id,
            f"Updated bank ledger entry #{entry_id}",
        )
        db.session.commit()
        flash("Ledger entry updated.", "success")
        return redirect(url_for("bank_ledger.bank_ledger", bank_id=entry.bank_id))

    return render_template(
        "bank_ledger/edit_entry.html",
        entry=entry,
        cancel_url=url_for("bank_ledger.bank_ledger", bank_id=entry.bank_id),
    )


@stock_edits_bp.route("/bank/transfer/<int:transfer_id>", methods=["GET", "POST"])
@login_required
def edit_bank_transfer(transfer_id):
    transfer = BankTransfer.query.get_or_404(transfer_id)
    banks = BankAccount.query.order_by(BankAccount.bank_name, BankAccount.account_number).all()

    if request.method == "POST":
        require_edit_access()
        transfer_date = request.form.get("transfer_date")
        from_bank_id = request.form.get("from_bank_id", type=int)
        to_bank_id = request.form.get("to_bank_id", type=int)
        amount = request.form.get("amount", type=float)
        reference = request.form.get("reference", "").strip()
        notes = request.form.get("notes", "").strip()

        if not transfer_date or not from_bank_id or not to_bank_id or not amount:
            flash("All transfer fields are required.", "danger")
            return redirect(url_for("stock_edits.edit_bank_transfer", transfer_id=transfer_id))

        try:
            update_bank_transfer(
                transfer,
                from_bank_id=from_bank_id,
                to_bank_id=to_bank_id,
                transfer_date=_parse_date(transfer_date),
                amount=amount,
                reference=reference or None,
                notes=notes or None,
            )
            log_audit(
                current_user.id,
                "UPDATE",
                "BankTransfer",
                transfer.id,
                f"Updated bank transfer #{transfer_id}",
            )
            db.session.commit()
            flash("Transfer updated on both bank ledgers.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("stock_edits.edit_bank_transfer", transfer_id=transfer_id))

        return redirect(url_for("bank_ledger.transfers"))

    return render_template(
        "bank_ledger/edit_transfer.html",
        transfer=transfer,
        banks=banks,
        cancel_url=url_for("bank_ledger.transfers"),
    )


# --- Purchase Receipts ---


def _edit_purchase_receipt(record_id, module, receipt_dir, redirect_endpoint, template_name):
    record = StockPurchaseReceipt.query.filter_by(id=record_id, module=module).first_or_404()

    if request.method == "POST":
        require_edit_access()
        receipt_date = request.form.get("receipt_date")
        company_id = request.form.get("company_id", type=int)
        title = request.form.get("title", "").strip()
        amount = request.form.get("amount", type=float)
        notes = request.form.get("notes", "").strip()
        screenshot = request.files.get("screenshot")

        if not receipt_date:
            flash("Receipt date is required.", "danger")
            return redirect(request.url)

        record.receipt_date = _parse_date(receipt_date)
        if module == StockPurchaseReceipt.MODULE_INK:
            if not company_id:
                flash("Company is required for ink receipts.", "danger")
                return redirect(request.url)
            record.company_id = company_id
        else:
            record.company_id = None
        record.title = title or None
        record.amount = amount
        record.notes = notes or None

        if module == StockPurchaseReceipt.MODULE_INK:
            record.inventory_transaction_id = (
                request.form.get("inventory_transaction_id", type=int) or None
            )
        else:
            record.material_transaction_id = (
                request.form.get("material_transaction_id", type=int) or None
            )

        if screenshot and screenshot.filename:
            try:
                prepared = save_receipt_upload(screenshot, receipt_dir)
                delete_receipt_file(record.screenshot_filename)
                apply_receipt_file(record, prepared)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(request.url)

        log_audit(
            current_user.id,
            "UPDATE",
            "StockPurchaseReceipt",
            record.id,
            f"Updated purchase receipt #{record_id}",
        )
        db.session.commit()
        flash("Purchase receipt updated.", "success")
        return redirect(url_for(redirect_endpoint))

    if module == StockPurchaseReceipt.MODULE_INK:
        companies = get_ink_companies()
        received_txns = InventoryTransaction.query.filter_by(
            transaction_type=InventoryTransaction.TRANSACTION_RECEIVED
        ).order_by(InventoryTransaction.transaction_date.desc()).limit(100).all()
        cancel_url = url_for("inventory.purchase_receipts")
        view_url = url_for("inventory.view_purchase_receipt", record_id=record.id)
    else:
        received_txns = MaterialTransaction.query.filter_by(
            transaction_type=MaterialTransaction.TRANSACTION_RECEIVED
        ).order_by(MaterialTransaction.transaction_date.desc()).limit(100).all()
        cancel_url = url_for("materials.purchase_receipts")
        view_url = url_for("materials.view_purchase_receipt", record_id=record.id)
        companies = []

    return render_template(
        template_name,
        record=record,
        companies=companies,
        received_txns=received_txns,
        cancel_url=cancel_url,
        view_url=view_url,
    )


@stock_edits_bp.route("/ink/purchase-receipt/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_ink_purchase_receipt(record_id):
    return _edit_purchase_receipt(
        record_id,
        StockPurchaseReceipt.MODULE_INK,
        INK_RECEIPT_DIR,
        "inventory.purchase_receipts",
        "inventory/edit_purchase_receipt.html",
    )


@stock_edits_bp.route("/materials/purchase-receipt/<int:record_id>", methods=["GET", "POST"])
@login_required
def edit_materials_purchase_receipt(record_id):
    return _edit_purchase_receipt(
        record_id,
        StockPurchaseReceipt.MODULE_MATERIALS,
        MATERIALS_RECEIPT_DIR,
        "materials.purchase_receipts",
        "materials/edit_purchase_receipt.html",
    )
