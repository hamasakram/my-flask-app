from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import Material, MaterialOpeningStock, MaterialTransaction, StockPurchaseReceipt
from app.services.inventory import log_audit
from app.services.receipt_uploads import apply_receipt_file, resolve_receipt_file, save_receipt_upload
from app.services.weights import parse_manual_weights
from app.services.materials_inventory import (
    calculate_live_stock,
    calculate_used_from_left_for_ref,
    create_material,
    get_current_stock_for_ref,
    get_material_options,
    get_stock_usage_records,
    is_valid_opening_stock_selection,
    resolve_material_selection,
    sync_opening_stock_material,
)

materials_bp = Blueprint("materials", __name__, url_prefix="/materials/inventory")
MATERIALS_RECEIPT_DIR = Path("uploads") / "materials" / "receipts"


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@materials_bp.route("/companies", methods=["GET", "POST"])
@login_required
def companies():
    flash("Companies are not used in Materials. Add materials directly in the catalog.", "info")
    return redirect(url_for("materials.catalog"))


@materials_bp.route("/catalog", methods=["GET", "POST"])
@login_required
def catalog():
    if request.method == "POST":
        require_edit_access()
        category = request.form.get("category", "PET").strip().upper()
        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()

        if not material_name or category not in ("PET", "METALIZE", "LD"):
            flash("Category (PET/METALIZE/LD) and item name are required.", "danger")
            return redirect(url_for("materials.catalog"))

        try:
            material = create_material(
                material_name, size, category=category, micron=micron
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

    materials = Material.query.order_by(Material.category, Material.name, Material.size).all()
    return render_template(
        "materials/catalog.html",
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
        sync_opening_stock_material(material_name)
        db.session.commit()
        flash("Opening stock saved successfully.", "success")
        return redirect(url_for("materials.opening_stock"))

    records = MaterialOpeningStock.query.order_by(MaterialOpeningStock.material_name).all()
    return render_template(
        "materials/opening_stock.html",
        records=records,
    )


@materials_bp.route("/opening-stock/clear-all", methods=["POST"])
@login_required
def clear_all_opening_stock():
    require_edit_access()
    count = MaterialOpeningStock.query.count()
    if count == 0:
        flash("Opening stock is already empty.", "info")
        return redirect(url_for("materials.opening_stock"))

    MaterialOpeningStock.query.delete(synchronize_session=False)
    log_audit(
        current_user.id,
        "DELETE",
        "MaterialOpeningStock",
        None,
        f"Cleared all {count} materials opening stock records",
    )
    db.session.commit()
    flash(f"All {count} opening stock records deleted.", "success")
    return redirect(url_for("materials.opening_stock"))


@materials_bp.route("/receive", methods=["GET", "POST"])
@login_required
def receive_stock():
    if request.method == "POST":
        require_edit_access()
        material_ref = request.form.get("material_id", "").strip()
        quantity = request.form.get("quantity", type=float)
        weights = parse_manual_weights(request.form)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not material_ref
            or not quantity
            or quantity <= 0
            or not transaction_date
        ):
            flash("Material, quantity, and date are required.", "danger")
            return redirect(url_for("materials.receive_stock"))

        material = resolve_material_selection(material_ref)
        if not material or not is_valid_opening_stock_selection(material_ref):
            flash(
                "Invalid material selection. Only opening stock materials can be purchased here.",
                "danger",
            )
            return redirect(url_for("materials.receive_stock"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = MaterialTransaction(
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
            f"Received {quantity} kg of {material.display_name}",
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
    material_options = get_material_options(context="receive")
    return render_template(
        "materials/receive_stock.html",
        material_options=material_options,
        recent_received=recent_received,
    )


@materials_bp.route("/use", methods=["GET", "POST"])
@login_required
def use_stock():
    if request.method == "POST":
        require_edit_access()
        material_ref = request.form.get("material_id", "").strip()
        quantity_left = request.form.get("quantity_left", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not material_ref
            or quantity_left is None
            or quantity_left < 0
            or not transaction_date
        ):
            flash("Material, quantity left (kg), and date are required.", "danger")
            return redirect(url_for("materials.use_stock"))

        material = resolve_material_selection(material_ref)
        if not material or not is_valid_opening_stock_selection(material_ref):
            flash("Invalid material selection. Only opening stock materials can be used here.", "danger")
            return redirect(url_for("materials.use_stock"))

        material_id = material.id

        try:
            quantity_used = calculate_used_from_left_for_ref(material_ref, quantity_left)
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
            f"Used {quantity_used} kg of {material.display_name} ({quantity_left} kg left)",
        )
        db.session.commit()
        flash(
            f"Daily usage recorded: {quantity_used:.1f} kg used, {quantity_left:.1f} kg left for '{material.display_name}'.",
            "success",
        )
        return redirect(url_for("materials.use_stock"))

    recent_usage = get_stock_usage_records()
    material_options = get_material_options(context="use")
    return render_template(
        "materials/use_stock.html",
        material_options=material_options,
        recent_usage=recent_usage,
        report_date=date.today().isoformat(),
    )


@materials_bp.route("/api/materials")
@login_required
def get_materials_api():
    context = request.args.get("context", "receive")
    if context not in {"receive", "use"}:
        return jsonify({"error": "Invalid context"}), 400
    return jsonify(get_material_options(context=context))


@materials_bp.route("/api/stock/<path:material_ref>")
@login_required
def get_material_stock(material_ref):
    material = resolve_material_selection(material_ref)
    if not material:
        return jsonify({"error": "Material not found"}), 404

    current = get_current_stock_for_ref(material_ref)
    opening_record = None
    if material_ref.startswith("opening:"):
        opening_id = int(material_ref.split(":", 1)[1])
        opening_record = MaterialOpeningStock.query.get(opening_id)

    return jsonify(
        {
            "current_stock": current,
            "material_name": (
                opening_record.material_name.strip()
                if opening_record
                else material.display_name
            ),
        }
    )


@materials_bp.route("/live")
@login_required
def live_inventory():
    material_search = request.args.get("material", "").strip().lower()

    rows = calculate_live_stock()
    if material_search:
        rows = [
            r
            for r in rows
            if material_search in r["material"].display_name.lower()
        ]

    return render_template(
        "materials/live_inventory.html",
        rows=rows,
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
    if request.method == "POST":
        require_edit_access()
        receipt_date = request.form.get("receipt_date")
        transaction_id = request.form.get("material_transaction_id", type=int) or None
        title = request.form.get("title", "").strip()
        amount = request.form.get("amount", type=float)
        notes = request.form.get("notes", "").strip()
        screenshot = request.files.get("screenshot")

        if not receipt_date:
            flash("Receipt date is required.", "danger")
            return redirect(url_for("materials.purchase_receipts"))

        try:
            prepared = save_receipt_upload(screenshot, MATERIALS_RECEIPT_DIR)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("materials.purchase_receipts"))

        record = StockPurchaseReceipt(
            module=StockPurchaseReceipt.MODULE_MATERIALS,
            receipt_date=datetime.strptime(receipt_date, "%Y-%m-%d").date(),
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
            "Materials purchase receipt uploaded",
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
        received_txns=received_txns,
    )
