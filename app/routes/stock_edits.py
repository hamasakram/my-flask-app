from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (
    ChemicalItem,
    ChemicalOpeningStock,
    ChemicalTransaction,
    GlueItem,
    GlueOpeningStock,
    GlueTransaction,
    Material,
    MaterialOpeningStock,
    MaterialTransaction,
    OpeningStock,
    InventoryTransaction,
)
from app.services.companies import (
    get_chemical_companies,
    get_glue_companies,
    get_ink_companies,
    get_material_companies,
)
from app.services.inventory import get_or_create_ink_type, log_audit
from app.services.weights import calculate_gross_net

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
        weight_per_quantity = request.form.get("weight_per_quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_name or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, ink name, valid quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_ink_received", txn_id=txn_id))

        try:
            ink = get_or_create_ink_type(
                company_id, ink_name, color_code=color_code, unit_type=unit_type
            )
            gross_weight, net_weight = calculate_gross_net(
                quantity, weight_per_quantity or 0
            )
            txn.company_id = company_id
            txn.ink_type_id = ink.id
            txn.quantity = quantity
            txn.weight_per_quantity = weight_per_quantity
            txn.gross_weight = gross_weight if weight_per_quantity else None
            txn.net_weight = net_weight if weight_per_quantity else None
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
        weight_per_quantity = request.form.get("weight_per_quantity", type=float)
        tw = request.form.get("tw", type=float) or 0
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not company_id
            or not material_id
            or not quantity
            or quantity <= 0
            or weight_per_quantity is None
            or not transaction_date
        ):
            flash("All required fields must be filled.", "danger")
            return redirect(url_for("stock_edits.edit_materials_received", txn_id=txn_id))

        material = Material.query.filter_by(id=material_id, company_id=company_id).first()
        if not material:
            flash("Invalid material selection.", "danger")
            return redirect(url_for("stock_edits.edit_materials_received", txn_id=txn_id))

        gross_weight, net_weight = calculate_gross_net(quantity, weight_per_quantity, tw)
        txn.company_id = company_id
        txn.material_id = material_id
        txn.quantity = quantity
        txn.weight_per_quantity = weight_per_quantity
        txn.gross_weight = gross_weight
        txn.tw = tw
        txn.net_weight = net_weight
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
        weight_per_quantity = request.form.get("weight_per_quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not item_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, item, quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_glue_received", txn_id=txn_id))

        item = GlueItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item selection.", "danger")
            return redirect(url_for("stock_edits.edit_glue_received", txn_id=txn_id))

        gross_weight, net_weight = calculate_gross_net(quantity, weight_per_quantity or 0)
        txn.company_id = company_id
        txn.item_id = item_id
        txn.quantity = quantity
        txn.weight_per_quantity = weight_per_quantity
        txn.gross_weight = gross_weight if weight_per_quantity else None
        txn.net_weight = net_weight if weight_per_quantity else None
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
        weight_per_quantity = request.form.get("weight_per_quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not item_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, item, quantity, and date are required.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_received", txn_id=txn_id))

        item = ChemicalItem.query.filter_by(id=item_id, company_id=company_id).first()
        if not item:
            flash("Invalid item selection.", "danger")
            return redirect(url_for("stock_edits.edit_chemicals_received", txn_id=txn_id))

        gross_weight, net_weight = calculate_gross_net(quantity, weight_per_quantity or 0)
        txn.company_id = company_id
        txn.item_id = item_id
        txn.quantity = quantity
        txn.weight_per_quantity = weight_per_quantity
        txn.gross_weight = gross_weight if weight_per_quantity else None
        txn.net_weight = net_weight if weight_per_quantity else None
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
