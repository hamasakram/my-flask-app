from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import Company, Material, MaterialOpeningStock, MaterialTransaction
from app.services.companies import get_material_companies
from app.services.inventory import log_audit
from app.services.materials_inventory import (
    calculate_live_stock,
    calculate_used_from_left,
    get_current_stock,
    get_or_create_material,
    get_stock_usage_records,
)

materials_bp = Blueprint("materials", __name__, url_prefix="/materials/inventory")


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
            material = get_or_create_material(
                company_id, material_name, size, category=category, micron=micron
            )
            log_audit(
                current_user.id,
                "CREATE",
                "Material",
                material.id,
                f"Material saved: {material.display_name}",
            )
            db.session.commit()
            flash(f"Material '{material.display_name}' saved for later use.", "success")
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
    companies = get_material_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        material_id = request.form.get("material_id", type=int)
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not material_id or quantity is None or not as_of_date:
            flash("Company, material, quantity (kg), and date are required.", "danger")
            return redirect(url_for("materials.opening_stock"))

        material = Material.query.filter_by(id=material_id, company_id=company_id).first()
        if not material:
            flash("Invalid material for this company.", "danger")
            return redirect(url_for("materials.opening_stock"))

        parsed_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        existing = MaterialOpeningStock.query.filter_by(
            company_id=company_id, material_id=material_id
        ).first()

        if existing:
            existing.quantity = quantity
            existing.as_of_date = parsed_date
            existing.notes = notes
            existing.created_by_id = current_user.id
            action = "UPDATE"
            entity_id = existing.id
        else:
            record = MaterialOpeningStock(
                company_id=company_id,
                material_id=material_id,
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
            f"{material.display_name}: opening stock set to {quantity} kg",
        )
        db.session.commit()
        flash("Opening stock saved successfully.", "success")
        return redirect(url_for("materials.opening_stock"))

    records = (
        MaterialOpeningStock.query.join(Company)
        .join(Material)
        .order_by(Company.name, Material.name)
        .all()
    )
    return render_template(
        "materials/opening_stock.html",
        companies=companies,
        records=records,
    )


@materials_bp.route("/receive", methods=["GET", "POST"])
@login_required
def receive_stock():
    companies = get_material_companies()

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
            or weight_per_quantity < 0
            or not transaction_date
        ):
            flash(
                "Company, material, quantity, weight per quantity, and date are required.",
                "danger",
            )
            return redirect(url_for("materials.receive_stock"))

        material = Material.query.filter_by(id=material_id, company_id=company_id).first()
        if not material:
            flash("Invalid material selection.", "danger")
            return redirect(url_for("materials.receive_stock"))

        gross_weight = quantity * weight_per_quantity
        net_weight = gross_weight - tw
        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = MaterialTransaction(
            company_id=company_id,
            material_id=material_id,
            transaction_type=MaterialTransaction.TRANSACTION_RECEIVED,
            quantity=quantity,
            weight_per_quantity=weight_per_quantity,
            gross_weight=gross_weight,
            tw=tw,
            net_weight=net_weight,
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

    return render_template("materials/receive_stock.html", companies=companies)


@materials_bp.route("/use", methods=["GET", "POST"])
@login_required
def use_stock():
    companies = get_material_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        material_id = request.form.get("material_id", type=int)
        quantity_left = request.form.get("quantity_left", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if (
            not company_id
            or not material_id
            or quantity_left is None
            or quantity_left < 0
            or not transaction_date
        ):
            flash("Company, material, quantity left (kg), and date are required.", "danger")
            return redirect(url_for("materials.use_stock"))

        material = Material.query.filter_by(id=material_id, company_id=company_id).first()
        if not material:
            flash("Invalid material selection.", "danger")
            return redirect(url_for("materials.use_stock"))

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
    materials = (
        Material.query.filter_by(company_id=company_id)
        .order_by(Material.name, Material.size)
        .all()
    )
    return jsonify(
        [
            {"id": m.id, "name": m.display_name, "size": m.size or ""}
            for m in materials
        ]
    )


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
