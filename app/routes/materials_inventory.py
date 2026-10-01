from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import db
from app.models import HallStockItem, HallStockMovement, Material, MaterialTransaction
from app.services.hall_stock import (
    add_or_increase_stock,
    list_items,
    list_movements,
    parse_stock_fields,
    record_machine_usage,
    transfer_to_machine_hall,
    update_item_fields,
)
from app.services.hall_stock_pdf import generate_hall_stock_pdf
from app.services.inventory import log_audit
from app.services.materials_inventory import (
    calculate_live_stock,
    calculate_used_from_left,
    create_material,
    ensure_production_job,
    get_current_stock,
    get_material_options,
    list_materials,
    list_production_job_names,
    parse_stock_left_usage_fields,
)

materials_bp = Blueprint("materials", __name__, url_prefix="/materials/inventory")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@materials_bp.route("/materials", methods=["GET", "POST"])
@login_required
def materials_list():
    if request.method == "POST":
        require_edit_access()
        category = request.form.get("category", "").strip()
        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()
        initial_kg = request.form.get("left_kg", type=float)
        if initial_kg is None:
            initial_kg = request.form.get("initial_kg", type=float) or 0

        try:
            material = create_material(category, material_name, size, micron, initial_kg)
            log_audit(
                current_user.id,
                "CREATE",
                "Material",
                material.id,
                f"Material added: {material.display_name} ({initial_kg:.1f} kg left)",
            )
            db.session.commit()
            flash(f"Material '{material.display_name}' added.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("materials.materials_list"))

    materials = list_materials()
    live_stock = {item["material"].id: item["current"] for item in calculate_live_stock()}
    return render_template(
        "materials/materials.html", materials=materials, live_stock=live_stock
    )


@materials_bp.route("/stock-in", methods=["GET", "POST"])
@login_required
def stock_in():
    materials = list_materials()

    if request.method == "POST":
        require_edit_access()
        material_id = request.form.get("material_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not material_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Material, quantity (kg), and date are required.", "danger")
            return redirect(url_for("materials.stock_in"))

        material = db.session.get(Material, material_id)
        if not material:
            flash("Invalid material.", "danger")
            return redirect(url_for("materials.stock_in"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = MaterialTransaction(
            company_id=material.company_id,
            material_id=material.id,
            transaction_type=MaterialTransaction.TRANSACTION_STOCK_IN,
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
            "MaterialTransaction",
            txn.id,
            f"Stock In {quantity} kg of {material.display_name}",
        )
        db.session.commit()
        flash(f"Stock In recorded: {quantity} kg of '{material.display_name}'.", "success")
        return redirect(url_for("materials.stock_in"))

    recent = (
        MaterialTransaction.query.filter_by(
            transaction_type=MaterialTransaction.TRANSACTION_STOCK_IN
        )
        .order_by(MaterialTransaction.transaction_date.desc(), MaterialTransaction.id.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "materials/stock_in.html", materials=materials, recent=recent
    )


@materials_bp.route("/stock-left", methods=["GET", "POST"])
@login_required
def stock_left():
    materials = list_materials()

    if request.method == "POST":
        require_edit_access()
        material_id = request.form.get("material_id", type=int)
        quantity_left = request.form.get("quantity_left", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()
        usage_fields = parse_stock_left_usage_fields(request.form)
        where_used = usage_fields["where_used"]

        if (
            material_id is None
            or quantity_left is None
            or quantity_left < 0
            or not transaction_date
        ):
            flash("Material, stock left (kg), and date are required.", "danger")
            return redirect(url_for("materials.stock_left"))

        material = db.session.get(Material, material_id)
        if not material:
            flash("Invalid material.", "danger")
            return redirect(url_for("materials.stock_left"))

        try:
            quantity_used = calculate_used_from_left(material.id, quantity_left)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("materials.stock_left"))

        if quantity_used <= 0:
            flash("No stock was used — left quantity matches current stock.", "info")
            return redirect(url_for("materials.stock_left"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        ensure_production_job(where_used)
        txn = MaterialTransaction(
            company_id=material.company_id,
            material_id=material.id,
            transaction_type=MaterialTransaction.TRANSACTION_STOCK_LEFT,
            quantity=quantity_used,
            quantity_left=quantity_left,
            where_used=where_used,
            used_in_printing=usage_fields["used_in_printing"],
            used_in_lamination=usage_fields["used_in_lamination"],
            printing_production=usage_fields["printing_production"],
            lamination_production=usage_fields["lamination_production"],
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
            f"Stock Left {quantity_left} kg ({quantity_used} kg used) of {material.display_name}",
        )
        db.session.commit()
        flash(
            f"Stock Left recorded: {quantity_used:.1f} kg used, {quantity_left:.1f} kg remaining.",
            "success",
        )
        return redirect(url_for("materials.stock_left"))

    recent = (
        MaterialTransaction.query.filter_by(
            transaction_type=MaterialTransaction.TRANSACTION_STOCK_LEFT
        )
        .order_by(MaterialTransaction.transaction_date.desc(), MaterialTransaction.id.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "materials/stock_left.html",
        materials=materials,
        recent=recent,
        job_names=list_production_job_names(),
    )


@materials_bp.route("/api/materials")
@login_required
def api_materials():
    return jsonify(get_material_options())


@materials_bp.route("/api/stock/<int:material_id>")
@login_required
def api_stock(material_id):
    material = db.session.get(Material, material_id)
    if not material:
        return jsonify({"error": "Material not found"}), 404
    return jsonify(
        {
            "current_stock": get_current_stock(material),
            "material_name": material.display_name,
        }
    )


def _save_hall_entry(location: str, redirect_endpoint: str):
    data = parse_stock_fields(request.form)
    item, created = add_or_increase_stock(location, data, current_user.id)
    db.session.flush()
    log_audit(
        current_user.id,
        "CREATE" if created else "UPDATE",
        "HallStockItem",
        item.id,
        f"{item.location_label}: {item.material_name}",
    )
    db.session.commit()
    if created:
        flash(f"{item.material_name} added to {item.location_label}.", "success")
    else:
        flash(f"Added onto existing {item.material_name} in {item.location_label}.", "success")
    return redirect(url_for(redirect_endpoint))


@materials_bp.route("/in-hall-stock", methods=["GET", "POST"])
@login_required
def in_hall_stock():
    if request.method == "POST":
        require_edit_access()
        action = request.form.get("action", "add")
        try:
            if action == "transfer":
                movement = transfer_to_machine_hall(request.form, current_user.id)
                db.session.flush()
                log_audit(
                    current_user.id,
                    "CREATE",
                    "HallStockMovement",
                    movement.id,
                    f"Transferred {movement.gross_kg} kg of {movement.material_name} to Machine Hall",
                )
                db.session.commit()
                flash(
                    f"Transferred {movement.gross_kg:,.3f} kg of {movement.material_name} to Machine Hall.",
                    "success",
                )
            else:
                return _save_hall_entry(HallStockItem.LOCATION_IN_HALL, "materials.in_hall_stock")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("materials.in_hall_stock"))

    return render_template(
        "materials/in_hall_stock.html",
        items=list_items(HallStockItem.LOCATION_IN_HALL),
        transfers=list_movements(HallStockMovement.TYPE_TRANSFER),
    )


@materials_bp.route("/in-hall-stock/pdf")
@login_required
def in_hall_stock_pdf():
    output = generate_hall_stock_pdf(
        "In Hall Stock",
        list_items(HallStockItem.LOCATION_IN_HALL),
        list_movements(HallStockMovement.TYPE_TRANSFER, limit=200),
        "Transfers to Machine Hall",
    )
    return send_file(
        output,
        as_attachment=True,
        download_name=f"in_hall_stock_{datetime.now().strftime('%Y%m%d')}.pdf",
        mimetype="application/pdf",
    )


@materials_bp.route("/machine-hall-stock", methods=["GET", "POST"])
@login_required
def machine_hall_stock():
    if request.method == "POST":
        require_edit_access()
        action = request.form.get("action", "add")
        try:
            if action == "use":
                movement = record_machine_usage(request.form, current_user.id)
                db.session.flush()
                log_audit(
                    current_user.id,
                    "CREATE",
                    "HallStockMovement",
                    movement.id,
                    f"Used {movement.material_name} at {movement.where_used}",
                )
                db.session.commit()
                flash(f"Usage recorded for {movement.material_name}.", "success")
            else:
                return _save_hall_entry(
                    HallStockItem.LOCATION_MACHINE, "materials.machine_hall_stock"
                )
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("materials.machine_hall_stock"))

    return render_template(
        "materials/machine_hall_stock.html",
        items=list_items(HallStockItem.LOCATION_MACHINE),
        usages=list_movements(HallStockMovement.TYPE_USED),
    )


@materials_bp.route("/machine-hall-stock/pdf")
@login_required
def machine_hall_stock_pdf():
    output = generate_hall_stock_pdf(
        "Machine Hall Stock",
        list_items(HallStockItem.LOCATION_MACHINE),
        list_movements(HallStockMovement.TYPE_USED, limit=200),
        "Material Used",
        include_where_used=True,
    )
    return send_file(
        output,
        as_attachment=True,
        download_name=f"machine_hall_stock_{datetime.now().strftime('%Y%m%d')}.pdf",
        mimetype="application/pdf",
    )


@materials_bp.route("/hall-stock/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_hall_stock(item_id):
    item = HallStockItem.query.get_or_404(item_id)
    back = (
        "materials.machine_hall_stock"
        if item.location == HallStockItem.LOCATION_MACHINE
        else "materials.in_hall_stock"
    )
    if request.method == "POST":
        require_edit_access()
        try:
            data = parse_stock_fields(request.form, require_quantity=False)
            update_item_fields(item, data)
            log_audit(
                current_user.id,
                "UPDATE",
                "HallStockItem",
                item.id,
                f"Updated {item.location_label} {item.material_name}",
            )
            db.session.commit()
            flash("Stock row updated.", "success")
            return redirect(url_for(back))
        except ValueError as exc:
            flash(str(exc), "danger")
    return render_template(
        "materials/edit_hall_stock.html",
        item=item,
        cancel_url=url_for(back),
    )


@materials_bp.route("/hall-stock/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_hall_stock(item_id):
    require_edit_access()
    item = HallStockItem.query.get_or_404(item_id)
    back = (
        "materials.machine_hall_stock"
        if item.location == HallStockItem.LOCATION_MACHINE
        else "materials.in_hall_stock"
    )
    HallStockMovement.query.filter(
        (HallStockMovement.source_item_id == item.id)
        | (HallStockMovement.dest_item_id == item.id)
    ).delete(synchronize_session=False)
    log_audit(
        current_user.id,
        "DELETE",
        "HallStockItem",
        item.id,
        f"Deleted {item.location_label} {item.material_name}",
    )
    db.session.delete(item)
    db.session.commit()
    flash("Stock row deleted.", "success")
    return redirect(url_for(back))
