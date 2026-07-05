from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (
    ShClientCompany,
    ShGatePassScreenshot,
    ShLedgerEntry,
    ShOpeningBalance,
    ShPartnerCompany,
    ShPaymentScreenshot,
    ShPurchase,
    ShSaleInvoice,
    ShSupplierCompany,
)
from app.services.inventory import log_audit
from app.services.sh_partnership import (
    apply_partnership_from_form,
    get_partner_ledger_balance,
)
from app.services.sh_sale_invoice import (
    compute_current_balance,
    next_sale_invoice_number,
    parse_invoice_lines,
    save_invoice_lines,
)
from app.services.sh_sale_invoice_pdf import generate_sale_invoice_pdf
from app.services.sh_traders import (
    calculate_total_amount,
    get_current_ledger_balance,
    get_dashboard_stats,
    get_ledger_rows,
    get_opening_balance,
    get_party_balance_totals,
    parse_multi_item_purchase_lines,
)
from app.services.sh_uploads import (
    apply_gate_pass_screenshot,
    apply_payment_screenshot,
    resolve_gate_pass_screenshot_file,
    resolve_payment_screenshot_file,
    save_gate_pass_screenshot,
    save_payment_screenshot,
)

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


@sh_main_bp.route("/partners", methods=["GET", "POST"])
@login_required
def partners():
    if request.method == "POST":
        require_edit_access()
        name = request.form.get("company_name", "").strip()
        if not name:
            flash("Partner name is required.", "danger")
            return redirect(url_for("sh_main.partners"))

        if ShPartnerCompany.query.filter(
            db.func.lower(ShPartnerCompany.name) == name.lower()
        ).first():
            flash("This partner already exists.", "warning")
            return redirect(url_for("sh_main.partners"))

        partner = ShPartnerCompany(name=name)
        db.session.add(partner)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShPartnerCompany",
            partner.id,
            f"SH partner added: {name}",
        )
        db.session.commit()
        flash(f"Partner '{name}' added — ledger is open for this partner.", "success")
        return redirect(url_for("sh_main.partners"))

    partner_list = ShPartnerCompany.query.order_by(ShPartnerCompany.name).all()
    summaries = [
        {
            "partner": partner,
            "ledger_balance": get_partner_ledger_balance(partner.id),
            "purchase_count": partner.purchase_shares.count(),
        }
        for partner in partner_list
    ]
    return render_template("sh_traders/partners.html", partners=summaries)


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
        client_id = request.form.get("client_company_id", type=int)
        notes = request.form.get("notes", "").strip()
        multi_mode = request.form.get("multi_mode") == "1"

        if not date_purchased or not supplier_id or not client_id:
            flash("Date, supplier, and purchased-for are required.", "danger")
            return redirect(url_for("sh_main.purchases"))

        parsed_date = _parse_date(date_purchased)

        if multi_mode:
            try:
                lines = parse_multi_item_purchase_lines(request.form)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("sh_main.purchases"))

            created = 0
            item_names = []
            for line in lines:
                client_rate = line["client_rate_per_kg"]
                total_kg = line["total_kg"]
                rate_per_kg = line["rate_per_kg"]
                purchase = ShPurchase(
                    date_purchased=parsed_date,
                    supplier_company_id=supplier_id,
                    material_name=line["material_name"],
                    size=line["size"],
                    micron=line["micron"] or None,
                    total_kg=total_kg,
                    rate_per_1000_kg=rate_per_kg,
                    total_amount=calculate_total_amount(total_kg, rate_per_kg),
                    paid_amount=0,
                    client_rate_per_kg=client_rate if client_rate > 0 else None,
                    client_total_amount=(
                        calculate_total_amount(total_kg, client_rate)
                        if client_rate > 0
                        else None
                    ),
                    client_company_id=client_id,
                    notes=notes or None,
                    created_by_id=current_user.id,
                )
                db.session.add(purchase)
                db.session.flush()
                try:
                    apply_partnership_from_form(purchase, request.form)
                except ValueError as exc:
                    db.session.rollback()
                    flash(str(exc), "danger")
                    return redirect(url_for("sh_main.purchases"))
                item_names.append(line["material_name"])
                created += 1

            log_audit(
                current_user.id,
                "CREATE",
                "ShPurchase",
                None,
                f"SH multi-item purchase: {created} items",
            )
            db.session.commit()
            flash(f"{created} purchase records saved ({', '.join(item_names[:3])}{'…' if created > 3 else ''}).", "success")
            return redirect(url_for("sh_main.purchases"))

        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()
        rate_per_1000 = request.form.get("rate_per_1000_kg", type=float)
        client_rate = request.form.get("client_rate_per_kg", type=float) or 0
        total_kg = request.form.get("total_kg", type=float)
        paid_amount = request.form.get("paid_amount", type=float) or 0

        if (
            not material_name
            or not rate_per_1000
            or rate_per_1000 <= 0
            or not total_kg
            or total_kg <= 0
        ):
            flash("Material, rate, and total KG are required.", "danger")
            return redirect(url_for("sh_main.purchases"))

        purchase = ShPurchase(
            date_purchased=parsed_date,
            supplier_company_id=supplier_id,
            material_name=material_name,
            size=size,
            micron=micron or None,
            total_kg=total_kg,
            rate_per_1000_kg=rate_per_1000,
            total_amount=calculate_total_amount(total_kg, rate_per_1000),
            paid_amount=paid_amount,
            client_rate_per_kg=client_rate if client_rate > 0 else None,
            client_total_amount=(
                calculate_total_amount(total_kg, client_rate) if client_rate > 0 else None
            ),
            client_company_id=client_id,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(purchase)
        db.session.flush()
        try:
            apply_partnership_from_form(purchase, request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("sh_main.purchases"))
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
    partner_list = ShPartnerCompany.query.order_by(ShPartnerCompany.name).all()
    return render_template(
        "sh_traders/purchases.html",
        purchases=purchase_list,
        suppliers=suppliers,
        clients=clients,
        partners=partner_list,
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
        partner_id = request.form.get("partner_company_id", type=int) or None
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
            partner_company_id=partner_id,
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
    partners = ShPartnerCompany.query.order_by(ShPartnerCompany.name).all()
    return render_template(
        "sh_traders/payments.html",
        opening=opening,
        ledger_rows=ledger_rows,
        current_balance=get_current_ledger_balance(),
        party_totals=party_totals,
        suppliers=suppliers,
        clients=clients,
        partners=partners,
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


@sh_main_bp.route("/gate-pass-screenshots/<int:record_id>/file")
@login_required
def view_gate_pass_screenshot(record_id):
    record = ShGatePassScreenshot.query.get_or_404(record_id)

    def backfill(rec, data, mimetype):
        rec.screenshot_data = data
        rec.screenshot_mimetype = mimetype
        db.session.commit()

    return resolve_gate_pass_screenshot_file(record, backfill_fn=backfill)


@sh_main_bp.route("/gate-pass-screenshots", methods=["GET", "POST"])
@login_required
def gate_pass_screenshots():
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    invoices = (
        ShSaleInvoice.query.order_by(ShSaleInvoice.invoice_date.desc(), ShSaleInvoice.id.desc()).all()
    )

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
            return redirect(url_for("sh_main.gate_pass_screenshots"))

        try:
            prepared = save_gate_pass_screenshot(screenshot)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sh_main.gate_pass_screenshots"))

        record = ShGatePassScreenshot(
            gate_pass_date=_parse_date(gate_pass_date),
            sold_to_client_id=sold_to_id,
            sale_invoice_id=sale_invoice_id,
            title=title or None,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        apply_gate_pass_screenshot(record, prepared)
        db.session.add(record)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShGatePassScreenshot",
            record.id,
            f"Gate pass screenshot for {gate_pass_date}",
        )
        db.session.commit()
        flash("Gate pass screenshot uploaded.", "success")
        return redirect(url_for("sh_main.gate_pass_screenshots"))

    records = (
        ShGatePassScreenshot.query.order_by(
            ShGatePassScreenshot.gate_pass_date.desc(),
            ShGatePassScreenshot.id.desc(),
        ).all()
    )
    return render_template(
        "sh_traders/gate_pass_screenshots.html",
        records=records,
        clients=clients,
        invoices=invoices,
    )


@sh_main_bp.route("/gate-passes")
@login_required
def gate_passes_redirect():
    return redirect(url_for("sh_main.sale_invoices"))


@sh_main_bp.route("/sale-invoices", methods=["GET", "POST"])
@login_required
def sale_invoices():
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        if not clients:
            flash("Add client companies first.", "danger")
            return redirect(url_for("sh_main.sale_invoices"))

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

        if not invoice_date or not sold_to_id:
            flash("Invoice date and sold to client are required.", "danger")
            return redirect(url_for("sh_main.sale_invoices"))

        try:
            parsed_date = datetime.strptime(invoice_date, "%Y-%m-%d").date()
            lines = parse_invoice_lines(request.form)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sh_main.sale_invoices"))

        invoice = ShSaleInvoice(
            invoice_number=invoice_number or next_sale_invoice_number(),
            invoice_date=parsed_date,
            factory_challan_no=factory_challan_no or None,
            sold_to_client_id=sold_to_id,
            location=location,
            previous_balance=previous_balance,
            previous_balance_type=previous_balance_type,
            current_balance_type=current_balance_type,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(invoice)
        db.session.flush()

        total_amount = save_invoice_lines(invoice, lines)
        invoice.total_amount = total_amount
        if current_balance_override is not None:
            invoice.current_balance = current_balance_override
        else:
            current, balance_type = compute_current_balance(
                previous_balance, total_amount, previous_balance_type
            )
            invoice.current_balance = current
            invoice.current_balance_type = balance_type

        log_audit(
            current_user.id,
            "CREATE",
            "ShSaleInvoice",
            invoice.id,
            f"Sale invoice {invoice.invoice_number}",
        )
        db.session.commit()
        flash(f"Sale invoice {invoice.invoice_number} created.", "success")
        return redirect(url_for("sh_main.sale_invoice_pdf", invoice_id=invoice.id))

    invoice_list = (
        ShSaleInvoice.query.order_by(ShSaleInvoice.invoice_date.desc(), ShSaleInvoice.id.desc()).all()
    )
    return render_template(
        "sh_traders/sale_invoices.html",
        invoices=invoice_list,
        clients=clients,
        next_invoice_number=next_sale_invoice_number(),
    )


@sh_main_bp.route("/sale-invoices/<int:invoice_id>/pdf")
@login_required
def sale_invoice_pdf(invoice_id):
    invoice = ShSaleInvoice.query.get_or_404(invoice_id)
    output = generate_sale_invoice_pdf(invoice)
    filename = f"sale_invoice_{invoice.invoice_number}.pdf"
    return send_file(
        output,
        as_attachment=False,
        download_name=filename,
        mimetype="application/pdf",
    )
