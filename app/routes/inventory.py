from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import Company, InkType, InventoryTransaction, OpeningStock, StockPurchaseReceipt
from app.services.companies import get_ink_companies
from app.services.inventory import (
    calculate_used_from_left,
    create_ink_type,
    get_recent_issued_records,
    get_recent_received_records,
    get_stored_stock,
    get_stock_usage_records,
    log_audit,
)
from app.services.receipt_uploads import apply_receipt_file, resolve_receipt_file, save_receipt_upload
from app.services.weights import parse_manual_weights

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")
INK_RECEIPT_DIR = Path("uploads") / "ink" / "receipts"


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@inventory_bp.route("/companies", methods=["GET", "POST"])
@login_required
def companies():
    if request.method == "POST":
        require_edit_access()
        company_name = request.form.get("company_name", "").strip()

        if not company_name:
            flash("Company name is required.", "danger")
            return redirect(url_for("inventory.companies"))

        existing = Company.query.filter_by(name=company_name).first()
        if existing:
            if existing.scope == Company.SCOPE_INK:
                flash("This company already exists in Ink Stock.", "warning")
            else:
                flash(
                    "This company name is already used in another module. Choose a different name.",
                    "danger",
                )
            return redirect(url_for("inventory.companies"))

        company = Company(name=company_name, scope=Company.SCOPE_INK)
        db.session.add(company)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "Company",
            company.id,
            f"Ink company added: {company_name}",
        )
        db.session.commit()
        flash(f"Company '{company_name}' added.", "success")
        return redirect(url_for("inventory.companies"))

    ink_companies = get_ink_companies()
    return render_template("inventory/companies.html", companies=ink_companies)


@inventory_bp.route("/stored")
@login_required
def stored_inventory():
    from app.services.inventory import calculate_live_stock

    company_id = request.args.get("company_id", type=int)
    ink_search = request.args.get("ink", "").strip().lower()

    rows = calculate_live_stock(company_id=company_id)
    if ink_search:
        rows = [r for r in rows if ink_search in r["ink_type"].name.lower()]

    companies = get_ink_companies()
    return render_template(
        "inventory/stored_inventory.html",
        rows=rows,
        companies=companies,
        selected_company=company_id,
        ink_search=request.args.get("ink", ""),
    )


@inventory_bp.route("/issue-to-use", methods=["GET", "POST"])
@login_required
def issue_to_use():
    companies = get_ink_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_type_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, ink, valid quantity, and date are required.", "danger")
            return redirect(url_for("inventory.issue_to_use"))

        ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
        if not ink:
            flash("Select a valid ink for this company.", "danger")
            return redirect(url_for("inventory.issue_to_use"))

        stored = get_stored_stock(company_id, ink_type_id)
        if quantity > stored:
            flash(
                f"Cannot issue {quantity:.1f} — only {stored:.1f} available in stored backup.",
                "danger",
            )
            return redirect(url_for("inventory.issue_to_use"))

        try:
            parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
            txn = InventoryTransaction(
                company_id=company_id,
                ink_type_id=ink.id,
                transaction_type=InventoryTransaction.TRANSACTION_ISSUED,
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
                f"Issued {quantity} of {ink.name} from stored to in-use",
            )
            db.session.commit()
            flash(
                f"Issued {quantity:.1f} units of '{ink.name}' from stored backup to in-use.",
                "success",
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        return redirect(url_for("inventory.issue_to_use"))

    recent_issued = get_recent_issued_records()
    return render_template(
        "inventory/issue_to_use.html",
        companies=companies,
        recent_issued=recent_issued,
    )


@inventory_bp.route("/in-use")
@login_required
def in_use_inventory():
    from app.services.inventory import calculate_live_stock

    company_id = request.args.get("company_id", type=int)
    ink_search = request.args.get("ink", "").strip().lower()

    rows = calculate_live_stock(company_id=company_id)
    if ink_search:
        rows = [r for r in rows if ink_search in r["ink_type"].name.lower()]

    companies = get_ink_companies()
    return render_template(
        "inventory/in_use_inventory.html",
        rows=rows,
        companies=companies,
        selected_company=company_id,
        ink_search=request.args.get("ink", ""),
    )


@inventory_bp.route("/catalog", methods=["GET", "POST"])
@login_required
def catalog():
    companies = get_ink_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        ink_name = request.form.get("ink_name", "").strip()
        color_code = request.form.get("color_code", "").strip()
        unit_type = request.form.get("unit_type", "").strip()

        if not company_id or not ink_name:
            flash("Company and ink name are required.", "danger")
            return redirect(url_for("inventory.catalog"))

        try:
            ink = create_ink_type(
                company_id, ink_name, color_code=color_code, unit_type=unit_type
            )
            log_audit(
                current_user.id,
                "CREATE",
                "InkType",
                ink.id,
                f"Ink added: {ink.name}",
            )
            db.session.commit()
            flash(f"Ink '{ink.name}' added to catalog.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        return redirect(url_for("inventory.catalog"))

    inks = (
        InkType.query.join(Company)
        .order_by(Company.name, InkType.name)
        .all()
    )
    return render_template(
        "inventory/catalog.html",
        companies=companies,
        inks=inks,
        unit_types=("Can", "Drum", "Tin"),
    )


@inventory_bp.route("/opening-stock", methods=["GET", "POST"])
@login_required
def opening_stock():
    companies = get_ink_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity = request.form.get("quantity", type=float)
        as_of_date = request.form.get("as_of_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_type_id or quantity is None or not as_of_date:
            flash("Company, ink, quantity, and date are required.", "danger")
            return redirect(url_for("inventory.opening_stock"))

        ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
        if not ink:
            flash("Select a valid ink for this company.", "danger")
            return redirect(url_for("inventory.opening_stock"))

        try:
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
    """Stock Received — select ink from catalog."""
    companies = get_ink_companies()

    if request.method == "POST":
        require_edit_access()
        company_id = request.form.get("company_id", type=int)
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not company_id or not ink_type_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Company, ink, valid quantity, and date are required.", "danger")
            return redirect(url_for("inventory.receive_stock"))

        ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
        if not ink:
            flash("Select a valid ink for this company.", "danger")
            return redirect(url_for("inventory.receive_stock"))

        try:
            parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
            weights = parse_manual_weights(request.form)

            txn = InventoryTransaction(
                company_id=company_id,
                ink_type_id=ink.id,
                transaction_type=InventoryTransaction.TRANSACTION_RECEIVED,
                quantity=quantity,
                weight_per_quantity=weights["weight_per_quantity"],
                gross_weight=weights["gross_weight"],
                tw=weights["tw"],
                net_weight=weights["net_weight"],
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

    recent_received = get_recent_received_records()
    return render_template(
        "receive_stock.html",
        companies=companies,
        unit_types=("Can", "Drum", "Tin"),
        recent_received=recent_received,
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

    from app.services.inventory import get_active_stock, get_stored_stock

    ink = InkType.query.filter_by(id=ink_type_id, company_id=company_id).first()
    if not ink:
        return jsonify({"error": "Ink not found"}), 404

    pool = request.args.get("pool", "active")
    if pool == "stored":
        current = get_stored_stock(company_id, ink_type_id)
    else:
        current = get_active_stock(company_id, ink_type_id)

    return jsonify({
        "current_stock": current,
        "stored_stock": get_stored_stock(company_id, ink_type_id),
        "active_stock": get_active_stock(company_id, ink_type_id),
        "ink_name": ink.name,
        "pool": pool,
    })


@inventory_bp.route("/api/inks/<int:company_id>")
@login_required
def get_company_inks(company_id):
    from flask import jsonify

    inks = (
        InkType.query.filter_by(company_id=company_id)
        .order_by(InkType.name)
        .all()
    )
    return jsonify(
        [
            {
                "id": ink.id,
                "name": ink.name,
                "color_code": ink.color_code or "",
                "unit_type": ink.unit_type or "",
            }
            for ink in inks
        ]
    )


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


@inventory_bp.route("/purchase-receipts/<int:record_id>/file")
@login_required
def view_purchase_receipt(record_id):
    record = StockPurchaseReceipt.query.filter_by(
        id=record_id, module=StockPurchaseReceipt.MODULE_INK
    ).first_or_404()

    def backfill(rec, data, mimetype):
        rec.screenshot_data = data
        rec.screenshot_mimetype = mimetype
        db.session.commit()

    return resolve_receipt_file(record, backfill=backfill)


@inventory_bp.route("/purchase-receipts", methods=["GET", "POST"])
@login_required
def purchase_receipts():
    companies = get_ink_companies()

    if request.method == "POST":
        require_edit_access()
        receipt_date = request.form.get("receipt_date")
        company_id = request.form.get("company_id", type=int)
        transaction_id = request.form.get("inventory_transaction_id", type=int) or None
        title = request.form.get("title", "").strip()
        amount = request.form.get("amount", type=float)
        notes = request.form.get("notes", "").strip()
        screenshot = request.files.get("screenshot")

        if not receipt_date or not company_id:
            flash("Receipt date and company are required.", "danger")
            return redirect(url_for("inventory.purchase_receipts"))

        try:
            prepared = save_receipt_upload(screenshot, INK_RECEIPT_DIR)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("inventory.purchase_receipts"))

        record = StockPurchaseReceipt(
            module=StockPurchaseReceipt.MODULE_INK,
            receipt_date=datetime.strptime(receipt_date, "%Y-%m-%d").date(),
            company_id=company_id,
            inventory_transaction_id=transaction_id,
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
            f"Ink purchase receipt for company #{company_id}",
        )
        db.session.commit()
        flash("Purchase receipt uploaded.", "success")
        return redirect(url_for("inventory.purchase_receipts"))

    records = (
        StockPurchaseReceipt.query.filter_by(module=StockPurchaseReceipt.MODULE_INK)
        .order_by(StockPurchaseReceipt.receipt_date.desc(), StockPurchaseReceipt.id.desc())
        .all()
    )
    received_txns = (
        InventoryTransaction.query.filter_by(
            transaction_type=InventoryTransaction.TRANSACTION_RECEIVED
        )
        .order_by(InventoryTransaction.transaction_date.desc(), InventoryTransaction.id.desc())
        .limit(100)
        .all()
    )
    return render_template(
        "inventory/purchase_receipts.html",
        records=records,
        companies=companies,
        received_txns=received_txns,
    )
