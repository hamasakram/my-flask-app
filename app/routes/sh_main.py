from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (
    ShClientCompany,
    ShGatePass,
    ShLedgerEntry,
    ShOpeningBalance,
    ShPaymentScreenshot,
    ShPurchase,
    ShSupplierCompany,
)
from app.services.inventory import log_audit
from app.services.sh_gate_pass_pdf import generate_gate_pass_pdf
from app.services.sh_traders import (
    calculate_gate_pass_total,
    calculate_total_amount,
    get_current_ledger_balance,
    get_dashboard_stats,
    get_ledger_rows,
    get_opening_balance,
    get_party_balance_totals,
    next_gate_pass_number,
)
from app.services.sh_uploads import apply_payment_screenshot, resolve_payment_screenshot_file, save_payment_screenshot

sh_main_bp = Blueprint("sh_main", __name__, url_prefix="/sh-traders")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


@sh_main_bp.route("/")
@login_required
def dashboard():
    from datetime import date

    stats = get_dashboard_stats(date.today())
    return render_template(
        "sh_traders/dashboard.html",
        stats=stats,
        today=date.today(),
    )


@sh_main_bp.route("/suppliers", methods=["GET", "POST"])
@login_required
def suppliers():
    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(url_for("sh_main.suppliers"))

        if ShSupplierCompany.query.filter_by(name=name).first():
            flash("This supplier company already exists.", "warning")
            return redirect(url_for("sh_main.suppliers"))

        company = ShSupplierCompany(name=name)
        db.session.add(company)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShSupplierCompany",
            company.id,
            f"SH supplier added: {name}",
        )
        db.session.commit()
        flash(f"Supplier '{name}' added.", "success")
        return redirect(url_for("sh_main.suppliers"))

    companies = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    return render_template("sh_traders/suppliers.html", companies=companies)


@sh_main_bp.route("/clients", methods=["GET", "POST"])
@login_required
def clients():
    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Company name is required.", "danger")
            return redirect(url_for("sh_main.clients"))

        if ShClientCompany.query.filter_by(name=name).first():
            flash("This client company already exists.", "warning")
            return redirect(url_for("sh_main.clients"))

        company = ShClientCompany(name=name)
        db.session.add(company)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShClientCompany",
            company.id,
            f"SH client added: {name}",
        )
        db.session.commit()
        flash(f"Client '{name}' added.", "success")
        return redirect(url_for("sh_main.clients"))

    companies = ShClientCompany.query.order_by(ShClientCompany.name).all()
    return render_template("sh_traders/clients.html", companies=companies)


@sh_main_bp.route("/purchases", methods=["GET", "POST"])
@login_required
def purchases():
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        if not suppliers:
            flash("Add at least one supplier company first.", "danger")
            return redirect(url_for("sh_main.suppliers"))
        if not clients:
            flash("Add at least one client company (Purchased For) first.", "danger")
            return redirect(url_for("sh_main.clients"))

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
            flash("Date, supplier, material, kg, rate, and purchased-for are required.", "danger")
            return redirect(url_for("sh_main.purchases"))

        total_amount = calculate_total_amount(total_kg, rate_per_1000)
        client_total_amount = calculate_total_amount(total_kg, client_rate) if client_rate > 0 else 0
        purchase = ShPurchase(
            date_purchased=_parse_date(date_purchased),
            supplier_company_id=supplier_id,
            material_name=material_name,
            size=size,
            micron=micron or None,
            total_kg=total_kg,
            rate_per_1000_kg=rate_per_1000,
            total_amount=total_amount,
            paid_amount=paid_amount,
            client_rate_per_kg=client_rate if client_rate > 0 else None,
            client_total_amount=client_total_amount if client_rate > 0 else None,
            client_company_id=client_id,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(purchase)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShPurchase",
            purchase.id,
            f"SH purchase: {material_name} {total_kg} kg",
        )
        db.session.commit()
        flash("Purchase recorded.", "success")
        return redirect(url_for("sh_main.purchases"))

    purchase_list = (
        ShPurchase.query.order_by(
            ShPurchase.date_purchased.desc(), ShPurchase.id.desc()
        ).all()
    )
    return render_template(
        "sh_traders/purchases.html",
        purchases=purchase_list,
        suppliers=suppliers,
        clients=clients,
    )


@sh_main_bp.route("/payments", methods=["GET", "POST"])
@login_required
def payments():
    opening = get_opening_balance()
    action = request.form.get("action", "ledger")

    if request.method == "POST":
        require_edit_access()

        if action == "opening":
            if opening:
                flash("Opening balance is already set. Use Edit to change it.", "warning")
                return redirect(url_for("sh_main.payments"))

            amount = request.form.get("opening_amount", type=float)
            notes = request.form.get("opening_notes", "").strip()
            if amount is None or amount < 0:
                flash("Enter a valid opening balance amount.", "danger")
                return redirect(url_for("sh_main.payments"))

            record = ShOpeningBalance(
                amount=amount,
                notes=notes or None,
                set_by_id=current_user.id,
            )
            db.session.add(record)
            db.session.flush()
            log_audit(
                current_user.id,
                "CREATE",
                "ShOpeningBalance",
                record.id,
                f"SH opening balance: {amount:,.2f}",
            )
            db.session.commit()
            flash("Opening balance saved.", "success")
            return redirect(url_for("sh_main.payments"))

        entry_date = request.form.get("entry_date")
        debit = request.form.get("debit", type=float) or 0
        credit = request.form.get("credit", type=float) or 0
        supplier_id = request.form.get("supplier_company_id", type=int) or None
        client_id = request.form.get("client_company_id", type=int) or None
        notes = request.form.get("notes", "").strip()

        if not entry_date:
            flash("Entry date is required.", "danger")
            return redirect(url_for("sh_main.payments"))

        if debit <= 0 and credit <= 0:
            flash("Enter a debit or credit amount.", "danger")
            return redirect(url_for("sh_main.payments"))

        if debit > 0 and credit > 0:
            flash("Enter either debit or credit, not both.", "danger")
            return redirect(url_for("sh_main.payments"))

        entry = ShLedgerEntry(
            entry_date=_parse_date(entry_date),
            debit=debit,
            credit=credit,
            supplier_company_id=supplier_id,
            client_company_id=client_id,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(entry)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShLedgerEntry",
            entry.id,
            f"SH ledger entry on {entry_date}",
        )
        db.session.commit()
        flash("Ledger entry added.", "success")
        return redirect(url_for("sh_main.payments"))

    ledger_rows = get_ledger_rows()
    party_totals = get_party_balance_totals()
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    return render_template(
        "sh_traders/payments.html",
        opening=opening,
        ledger_rows=ledger_rows,
        current_balance=get_current_ledger_balance(),
        party_totals=party_totals,
        suppliers=suppliers,
        clients=clients,
    )


@sh_main_bp.route("/party-balances")
@login_required
def party_balances():
    party_totals = get_party_balance_totals()
    return render_template(
        "sh_traders/party_balances.html",
        party_totals=party_totals,
    )


@sh_main_bp.route("/payment-screenshots/<int:record_id>/file")
@login_required
def view_payment_screenshot(record_id):
    record = ShPaymentScreenshot.query.get_or_404(record_id)

    def backfill(rec, data, mimetype):
        rec.screenshot_data = data
        rec.screenshot_mimetype = mimetype
        db.session.commit()

    return resolve_payment_screenshot_file(record, backfill_fn=backfill)


@sh_main_bp.route("/payment-screenshots", methods=["GET", "POST"])
@login_required
def payment_screenshots():
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    purchases = (
        ShPurchase.query.order_by(ShPurchase.date_purchased.desc(), ShPurchase.id.desc()).all()
    )

    if request.method == "POST":
        require_edit_access()
        if not suppliers:
            flash("Add at least one supplier company first.", "danger")
            return redirect(url_for("sh_main.suppliers"))

        payment_date = request.form.get("payment_date")
        supplier_id = request.form.get("supplier_company_id", type=int)
        amount_paid = request.form.get("amount_paid", type=float)
        purchase_id = request.form.get("purchase_id", type=int) or None
        notes = request.form.get("notes", "").strip()
        screenshot = request.files.get("screenshot")

        if not payment_date or not supplier_id:
            flash("Payment date and supplier are required.", "danger")
            return redirect(url_for("sh_main.payment_screenshots"))

        try:
            prepared = save_payment_screenshot(screenshot)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sh_main.payment_screenshots"))

        record = ShPaymentScreenshot(
            payment_date=_parse_date(payment_date),
            supplier_company_id=supplier_id,
            amount_paid=amount_paid,
            purchase_id=purchase_id,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        apply_payment_screenshot(record, prepared)
        db.session.add(record)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShPaymentScreenshot",
            record.id,
            f"Payment screenshot for supplier #{supplier_id}",
        )
        db.session.commit()
        flash("Payment screenshot uploaded.", "success")
        return redirect(url_for("sh_main.payment_screenshots"))

    records = (
        ShPaymentScreenshot.query.order_by(
            ShPaymentScreenshot.payment_date.desc(), ShPaymentScreenshot.id.desc()
        ).all()
    )
    return render_template(
        "sh_traders/payment_screenshots.html",
        records=records,
        suppliers=suppliers,
        purchases=purchases,
    )


@sh_main_bp.route("/gate-passes", methods=["GET", "POST"])
@login_required
def gate_passes():
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    purchases = (
        ShPurchase.query.order_by(ShPurchase.date_purchased.desc(), ShPurchase.id.desc()).all()
    )

    if request.method == "POST":
        require_edit_access()
        if not suppliers or not clients:
            flash("Add supplier and client companies first.", "danger")
            return redirect(url_for("sh_main.gate_passes"))

        issued_date = request.form.get("issued_date")
        issued_time = request.form.get("issued_time")
        sold_to_id = request.form.get("sold_to_client_id", type=int)
        supplier_id = request.form.get("supplier_company_id", type=int)
        purchase_id = request.form.get("purchase_id", type=int) or None
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
            flash("Date, time, sold to, supplier, material, weights, and amount per KG are required.", "danger")
            return redirect(url_for("sh_main.gate_passes"))

        issued_at = datetime.strptime(f"{issued_date} {issued_time}", "%Y-%m-%d %H:%M")
        total_amount = calculate_gate_pass_total(net_weight, amount_per_kg)

        gate_pass = ShGatePass(
            gate_pass_number=next_gate_pass_number(),
            issued_at=issued_at,
            sold_to_client_id=sold_to_id,
            supplier_company_id=supplier_id,
            purchase_id=purchase_id,
            material_name=material_name,
            size=size,
            micron=micron or None,
            rolls=rolls,
            gross_weight_per_roll=gross_weight_per_roll,
            net_weight_per_roll=net_weight_per_roll,
            gross_weight=gross_weight,
            net_weight=net_weight,
            amount_per_kg=amount_per_kg,
            total_amount=total_amount,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(gate_pass)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShGatePass",
            gate_pass.id,
            f"Gate pass {gate_pass.gate_pass_number}",
        )
        db.session.commit()
        flash(f"Gate pass {gate_pass.gate_pass_number} created.", "success")
        return redirect(url_for("sh_main.gate_pass_pdf", gate_pass_id=gate_pass.id))

    gate_pass_list = (
        ShGatePass.query.order_by(ShGatePass.issued_at.desc(), ShGatePass.id.desc()).all()
    )
    return render_template(
        "sh_traders/gate_passes.html",
        gate_passes=gate_pass_list,
        suppliers=suppliers,
        clients=clients,
        purchases=purchases,
    )


@sh_main_bp.route("/gate-passes/<int:gate_pass_id>/pdf")
@login_required
def gate_pass_pdf(gate_pass_id):
    gate_pass = ShGatePass.query.get_or_404(gate_pass_id)
    output = generate_gate_pass_pdf(gate_pass)
    filename = f"gate_pass_{gate_pass.gate_pass_number}.pdf"
    return send_file(
        output,
        as_attachment=False,
        download_name=filename,
        mimetype="application/pdf",
    )
