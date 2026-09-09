from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (
    ShBank,
    ShClientCompany,
    ShClientLedgerEntry,
    ShGatePassScreenshot,
    ShInvestorPurchase,
    ShInvestorSale,
    ShInvestorSalePayment,
    ShLedgerEntry,
    ShOrderConfirmation,
    ShPartnerCompany,
    ShPaymentReceipt,
    ShPaymentScreenshot,
    ShProfitLossRecord,
    ShPurchase,
    ShSaleInvoice,
    ShSupplierCompany,
    ShSupplierLedgerEntry,
)
from app.services.inventory import log_audit
from app.services.sh_bank import (
    ensure_bank_on_create,
    filter_by_bank,
    get_all_banks,
    get_current_sh_bank,
    set_current_sh_bank,
)
from app.services.sh_client_ledger_report import (
    build_client_ledger_timeline,
    generate_complete_client_ledger_pdf,
)
from app.services.sh_ledger_pdf import generate_client_ledger_pdf, generate_supplier_ledger_pdf
from app.services.sh_ledger_sync import (
    compute_invoice_balances,
    get_client_ledger_balance_before,
    get_invoice_remaining,
    get_last_client_ledger_balance,
    get_last_supplier_ledger_balance,
    get_supplier_ledger_balance_before,
    recalculate_client_ledger_chain,
    recalculate_supplier_ledger_chain,
    sync_bank_entry_to_party_ledgers,
    sync_unsynced_bank_payments,
)
from app.services.sh_manual_ledger import (
    build_client_ledger_from_form,
    build_supplier_ledger_from_form,
    get_client_ledger_entries,
    get_supplier_ledger_entries,
    group_client_ledger_by_party,
    group_supplier_ledger_by_party,
    next_client_ledger_ref,
    next_supplier_ledger_ref,
)
from app.services.sh_order_confirmation_pdf import generate_order_confirmation_pdf
from app.services.sh_partnership import apply_partnership_from_form, get_partner_ledger_balance
from app.services.sh_payment_receipt import (
    get_payment_receipts,
    next_payment_receipt_number,
    suggest_total_received,
)
from app.services.sh_payment_receipt_pdf import generate_payment_receipt_pdf
from app.services.sh_sale_invoice import (
    compute_current_balance,
    next_sale_invoice_number,
    parse_invoice_lines,
    resolve_sale_invoice_previous_balance,
    save_invoice_lines,
)
from app.services.sh_sale_invoice_pdf import generate_sale_invoice_pdf
from app.services.sh_profit_loss import (
    apply_record_fields,
    build_record_from_form,
    get_dashboard_stats as get_profit_loss_dashboard_stats,
    scoped_records_query,
)
from app.services.sh_profit_loss_pdf import (
    generate_profit_loss_record_pdf,
    generate_profit_loss_report_pdf,
)
from app.services.sh_investor_stock import (
    apply_payment_fields,
    apply_purchase_fields,
    apply_sale_fields,
    build_page_context,
    build_payment_from_form,
    build_purchase_from_form,
    build_sale_from_form,
)
from app.services.sh_traders import (
    calculate_total_amount,
    get_current_ledger_balance,
    get_ledger_rows,
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


def _bank_purchases():
    return filter_by_bank(ShPurchase.query, ShPurchase).order_by(
        ShPurchase.date_purchased.desc(), ShPurchase.id.desc()
    )


def _bank_sale_invoices():
    return filter_by_bank(ShSaleInvoice.query, ShSaleInvoice).order_by(
        ShSaleInvoice.invoice_date.desc(), ShSaleInvoice.id.desc()
    )


@sh_main_bp.route("/select-bank", methods=["POST"])
@login_required
def select_bank():
    bank_id = request.form.get("bank_id", type=int)
    if bank_id and set_current_sh_bank(bank_id):
        flash(f"Switched to {get_current_sh_bank().name}.", "success")
    else:
        flash("Invalid bank selection.", "danger")
    return redirect(request.referrer or url_for("sh_main.dashboard"))


@sh_main_bp.route("/banks", methods=["GET", "POST"])
@login_required
def banks():
    if request.method == "POST":
        require_edit_access()
        action = request.form.get("action", "add")

        if action == "opening":
            bank = get_current_sh_bank()
            if not bank:
                flash("No bank selected.", "danger")
                return redirect(url_for("sh_main.banks"))
            amount = request.form.get("opening_balance", type=float)
            if amount is None or amount < 0:
                flash("Enter a valid opening balance.", "danger")
                return redirect(url_for("sh_main.banks"))
            bank.opening_balance = amount
            log_audit(
                current_user.id,
                "UPDATE",
                "ShBank",
                bank.id,
                f"Updated opening balance for {bank.name}: {amount:,.2f}",
            )
            db.session.commit()
            flash(f"Opening balance updated for {bank.name}.", "success")
            return redirect(url_for("sh_main.banks"))

        name = request.form.get("bank_name", "").strip()
        opening = request.form.get("opening_balance", type=float) or 0.0
        if not name:
            flash("Bank name is required.", "danger")
            return redirect(url_for("sh_main.banks"))
        if ShBank.query.filter(db.func.lower(ShBank.name) == name.lower()).first():
            flash("This bank already exists.", "warning")
            return redirect(url_for("sh_main.banks"))

        is_first = ShBank.query.count() == 0
        bank = ShBank(name=name, opening_balance=opening, is_default=is_first)
        db.session.add(bank)
        db.session.flush()
        log_audit(current_user.id, "CREATE", "ShBank", bank.id, f"SH bank added: {name}")
        db.session.commit()
        if is_first:
            set_current_sh_bank(bank.id)
        flash(f"Bank '{name}' added.", "success")
        return redirect(url_for("sh_main.banks"))

    return render_template(
        "sh_traders/banks.html",
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/")
@login_required
def dashboard():
    from datetime import date

    stats = get_profit_loss_dashboard_stats(date.today())
    return render_template(
        "sh_traders/dashboard.html",
        stats=stats,
        today=date.today(),
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
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
    return render_template(
        "sh_traders/suppliers.html",
        companies=companies,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


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
    return render_template(
        "sh_traders/clients.html",
        companies=companies,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/partners", methods=["GET", "POST"])
@login_required
def partners():
    if request.method == "POST":
        require_edit_access()
        action = request.form.get("action", "add_partner")

        if action == "partner_payment":
            entry_date = request.form.get("entry_date")
            partner_id = request.form.get("partner_company_id", type=int)
            debit = request.form.get("debit", type=float) or 0
            credit = request.form.get("credit", type=float) or 0
            notes = request.form.get("notes", "").strip()

            if not entry_date or not partner_id:
                flash("Date and partner are required.", "danger")
                return redirect(url_for("sh_main.partners"))
            if debit <= 0 and credit <= 0:
                flash("Enter a debit or credit amount.", "danger")
                return redirect(url_for("sh_main.partners"))
            if debit > 0 and credit > 0:
                flash("Enter either debit or credit, not both.", "danger")
                return redirect(url_for("sh_main.partners"))

            entry = ShLedgerEntry(
                entry_date=_parse_date(entry_date),
                debit=debit,
                credit=credit,
                partner_company_id=partner_id,
                notes=notes or None,
                created_by_id=current_user.id,
            )
            ensure_bank_on_create(entry)
            db.session.add(entry)
            db.session.flush()
            log_audit(
                current_user.id,
                "CREATE",
                "ShLedgerEntry",
                entry.id,
                f"Partner ledger entry on {entry_date}",
            )
            db.session.commit()
            flash("Partner payment recorded.", "success")
            return redirect(url_for("sh_main.partners"))

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
    return render_template(
        "sh_traders/partners.html",
        partners=summaries,
        partner_list=partner_list,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/purchases")
@login_required
def purchases():
    return redirect(url_for("sh_main.profit_loss"))


@sh_main_bp.route("/profit-loss", methods=["GET", "POST"])
@login_required
def profit_loss():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    if request.method == "POST":
        require_edit_access()
        try:
            data = build_record_from_form(request.form)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sh_main.profit_loss"))

        record = ShProfitLossRecord(created_by_id=current_user.id)
        ensure_bank_on_create(record)
        apply_record_fields(record, data, _parse_date(data["record_date"]))
        db.session.add(record)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShProfitLossRecord",
            record.id,
            f"SH P/L: {record.material_name} ({record.broker_label})",
        )
        db.session.commit()
        flash("Profit / loss record saved.", "success")
        return redirect(url_for("sh_main.profit_loss"))

    records = scoped_records_query().all()
    from app.services.sh_profit_loss import get_broker_summaries

    return render_template(
        "sh_traders/profit_loss.html",
        records=records,
        broker_summaries=get_broker_summaries(records),
        brokers=ShProfitLossRecord.BROKER_LABELS,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/profit-loss/pdf")
@login_required
def profit_loss_report_pdf():
    broker = request.args.get("broker")
    if broker and broker not in ShProfitLossRecord.BROKER_LABELS:
        abort(400)
    output = generate_profit_loss_report_pdf(broker=broker or None)
    suffix = f"_{broker}" if broker else ""
    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"sh_profit_loss{suffix}_{datetime.now().strftime('%Y%m%d')}.pdf",
    )


@sh_main_bp.route("/profit-loss/<int:record_id>/pdf")
@login_required
def profit_loss_record_pdf(record_id):
    record = ShProfitLossRecord.query.get_or_404(record_id)
    output = generate_profit_loss_record_pdf(record)
    safe_name = record.material_name.replace(" ", "_")[:30]
    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"pl_{record.id}_{safe_name}.pdf",
    )


@sh_main_bp.route("/investor-stock", methods=["GET", "POST"])
@login_required
def investor_stock():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    if request.method == "POST":
        require_edit_access()
        action = request.form.get("action", "record_purchase")
        try:
            if action == "record_purchase":
                data = build_purchase_from_form(request.form)
                purchase = ShInvestorPurchase(created_by_id=current_user.id)
                ensure_bank_on_create(purchase)
                apply_purchase_fields(purchase, data, _parse_date(data["purchase_date"]))
                db.session.add(purchase)
                db.session.flush()
                log_audit(
                    current_user.id,
                    "CREATE",
                    "ShInvestorPurchase",
                    purchase.id,
                    f"Investor purchase: {purchase.material_name}",
                )
                db.session.commit()
                flash("Investor purchase recorded.", "success")

            elif action == "record_sale":
                data = build_sale_from_form(request.form)
                sale = ShInvestorSale(created_by_id=current_user.id)
                ensure_bank_on_create(sale)
                apply_sale_fields(sale, data, _parse_date(data["sale_date"]))
                db.session.add(sale)
                db.session.flush()
                log_audit(
                    current_user.id,
                    "CREATE",
                    "ShInvestorSale",
                    sale.id,
                    f"Investor sale {sale.invoice_number}",
                )
                db.session.commit()
                flash("Investor sale recorded.", "success")

            elif action == "record_payment":
                data = build_payment_from_form(request.form)
                payment = ShInvestorSalePayment(created_by_id=current_user.id)
                ensure_bank_on_create(payment)
                apply_payment_fields(payment, data, _parse_date(data["payment_date"]))
                db.session.add(payment)
                db.session.flush()
                log_audit(
                    current_user.id,
                    "CREATE",
                    "ShInvestorSalePayment",
                    payment.id,
                    f"Investor payment on sale #{data['sale_id']}",
                )
                db.session.commit()
                flash("Payment applied to investor sale invoice.", "success")
            else:
                flash("Unknown action.", "danger")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("sh_main.investor_stock"))

    ctx = build_page_context()
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    return render_template(
        "sh_traders/investor_stock.html",
        clients=clients,
        banks=get_all_banks(),
        **ctx,
    )


@sh_main_bp.route("/payments", methods=["GET", "POST"])
@login_required
def payments():
    if not get_current_sh_bank():
        flash("Add a bank first (e.g. Askari Bank).", "warning")
        return redirect(url_for("sh_main.banks"))

    bank = get_current_sh_bank()
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    partners = ShPartnerCompany.query.order_by(ShPartnerCompany.name).all()

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
        ensure_bank_on_create(entry)
        db.session.add(entry)
        db.session.flush()
        sync_messages = sync_bank_entry_to_party_ledgers(entry, current_user.id)
        log_audit(
            current_user.id,
            "CREATE",
            "ShLedgerEntry",
            entry.id,
            f"Bank ledger entry on {entry_date}",
        )
        db.session.commit()
        flash("Ledger entry added.", "success")
        for msg in sync_messages:
            flash(msg, "info")
        return redirect(url_for("sh_main.payments"))

    return render_template(
        "sh_traders/payments.html",
        bank=bank,
        ledger_rows=get_ledger_rows(),
        current_balance=get_current_ledger_balance(),
        suppliers=suppliers,
        clients=clients,
        partners=partners,
        banks=get_all_banks(),
        current_bank=bank,
    )


@sh_main_bp.route("/api/party-balance")
@login_required
def party_balance_api():
    party_type = request.args.get("type", "")
    party_id = request.args.get("id", type=int)
    date_str = request.args.get("date", "").strip()
    before_date = None
    if date_str:
        try:
            before_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            before_date = None

    if party_type == "client" and party_id:
        if before_date:
            balance, balance_type = get_client_ledger_balance_before(party_id, before_date)
        else:
            balance, balance_type = get_last_client_ledger_balance(party_id)
    elif party_type == "supplier" and party_id:
        if before_date:
            balance, balance_type = get_supplier_ledger_balance_before(party_id, before_date)
        else:
            balance, balance_type = get_last_supplier_ledger_balance(party_id)
    else:
        balance, balance_type = 0.0, "DR"
    return jsonify({"balance": balance, "balance_type": balance_type})


@sh_main_bp.route("/party-balances")
@login_required
def party_balances_redirect():
    return redirect(url_for("sh_main.client_ledger"))


@sh_main_bp.route("/sync-party-ledgers", methods=["POST"])
@login_required
def sync_party_ledgers():
    require_edit_access()
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    stats = sync_unsynced_bank_payments()
    synced = stats["client_payments"] + stats["supplier_payments"]
    if synced:
        flash(
            f"Synced {stats['client_payments']} client and "
            f"{stats['supplier_payments']} supplier payment(s) from bank ledger. "
            "All balances recalculated.",
            "success",
        )
    else:
        flash("All bank payments are already synced. Balances recalculated.", "success")
    return redirect(request.referrer or url_for("sh_main.client_ledger"))


@sh_main_bp.route("/client-ledger", methods=["GET", "POST"])
@login_required
def client_ledger():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        if not clients:
            flash("Add client companies first.", "danger")
            return redirect(url_for("sh_main.clients"))
        try:
            entry = build_client_ledger_from_form(request.form, current_user.id)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sh_main.client_ledger"))

        log_audit(
            current_user.id,
            "CREATE",
            "ShClientLedgerEntry",
            entry.id,
            f"Client ledger entry {entry.reference_number}",
        )
        recalculate_client_ledger_chain(entry.sold_to_client_id, entry.bank_id)
        db.session.commit()
        flash(f"Client ledger entry {entry.reference_number} saved.", "success")
        return redirect(url_for("sh_main.client_ledger_pdf"))

    return render_template(
        "sh_traders/client_ledger.html",
        grouped_parties=group_client_ledger_by_party(),
        clients=clients,
        next_reference=next_client_ledger_ref(),
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/client-ledger/pdf")
@login_required
def client_ledger_pdf():
    grouped = group_client_ledger_by_party()
    output = generate_client_ledger_pdf(grouped)
    bank = get_current_sh_bank()
    bank_slug = (bank.name if bank else "ledger").replace(" ", "_")
    return send_file(
        output,
        as_attachment=False,
        download_name=f"client_ledger_{bank_slug}.pdf",
        mimetype="application/pdf",
    )


@sh_main_bp.route("/client-ledger/pdf/<int:client_id>")
@login_required
def client_ledger_party_pdf(client_id):
    grouped = group_client_ledger_by_party(client_id)
    if not grouped:
        flash("No ledger entries for this client.", "warning")
        return redirect(url_for("sh_main.client_ledger"))
    client = grouped[0]["party"]
    output = generate_client_ledger_pdf(grouped)
    bank = get_current_sh_bank()
    bank_slug = (bank.name if bank else "ledger").replace(" ", "_")
    client_slug = client.name.replace(" ", "_")
    return send_file(
        output,
        as_attachment=False,
        download_name=f"client_ledger_{client_slug}_{bank_slug}.pdf",
        mimetype="application/pdf",
    )


@sh_main_bp.route("/client-ledger-report", methods=["GET"])
@login_required
def client_ledger_report():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    client_id = request.args.get("client_id", type=int)
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()

    preview = None
    if client_id:
        date_from = _parse_date(date_from_str) if date_from_str else None
        date_to = _parse_date(date_to_str) if date_to_str else None
        preview = build_client_ledger_timeline(client_id, date_from, date_to)

    return render_template(
        "sh_traders/client_ledger_report.html",
        clients=clients,
        selected_client_id=client_id,
        date_from=date_from_str,
        date_to=date_to_str,
        preview=preview,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/client-ledger-report/pdf")
@login_required
def client_ledger_report_pdf():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    client_id = request.args.get("client_id", type=int)
    if not client_id:
        flash("Select a client for the ledger report.", "danger")
        return redirect(url_for("sh_main.client_ledger_report"))

    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()
    date_from = _parse_date(date_from_str) if date_from_str else None
    date_to = _parse_date(date_to_str) if date_to_str else None

    report = build_client_ledger_timeline(client_id, date_from, date_to)
    output = generate_complete_client_ledger_pdf(report)
    client_slug = report["client"].name.replace(" ", "_")
    range_suffix = ""
    if date_from and date_to:
        range_suffix = f"_{date_from.strftime('%Y%m%d')}_{date_to.strftime('%Y%m%d')}"
    elif date_from:
        range_suffix = f"_from_{date_from.strftime('%Y%m%d')}"
    elif date_to:
        range_suffix = f"_to_{date_to.strftime('%Y%m%d')}"

    return send_file(
        output,
        as_attachment=True,
        download_name=f"ledger_report_{client_slug}{range_suffix}.pdf",
        mimetype="application/pdf",
    )


@sh_main_bp.route("/supplier-ledger", methods=["GET", "POST"])
@login_required
def supplier_ledger():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        if not suppliers:
            flash("Add supplier companies first.", "danger")
            return redirect(url_for("sh_main.suppliers"))
        try:
            entry = build_supplier_ledger_from_form(request.form, current_user.id)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sh_main.supplier_ledger"))

        log_audit(
            current_user.id,
            "CREATE",
            "ShSupplierLedgerEntry",
            entry.id,
            f"Supplier ledger entry {entry.reference_number}",
        )
        recalculate_supplier_ledger_chain(entry.supplier_company_id, entry.bank_id)
        db.session.commit()
        flash(f"Supplier ledger entry {entry.reference_number} saved.", "success")
        return redirect(url_for("sh_main.supplier_ledger_pdf"))

    return render_template(
        "sh_traders/supplier_ledger.html",
        grouped_parties=group_supplier_ledger_by_party(),
        suppliers=suppliers,
        next_reference=next_supplier_ledger_ref(),
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/supplier-ledger/pdf")
@login_required
def supplier_ledger_pdf():
    grouped = group_supplier_ledger_by_party()
    output = generate_supplier_ledger_pdf(grouped)
    bank = get_current_sh_bank()
    bank_slug = (bank.name if bank else "ledger").replace(" ", "_")
    return send_file(
        output,
        as_attachment=False,
        download_name=f"supplier_ledger_{bank_slug}.pdf",
        mimetype="application/pdf",
    )


@sh_main_bp.route("/supplier-ledger/pdf/<int:supplier_id>")
@login_required
def supplier_ledger_party_pdf(supplier_id):
    grouped = group_supplier_ledger_by_party(supplier_id)
    if not grouped:
        flash("No ledger entries for this supplier.", "warning")
        return redirect(url_for("sh_main.supplier_ledger"))
    supplier = grouped[0]["party"]
    output = generate_supplier_ledger_pdf(grouped)
    bank = get_current_sh_bank()
    bank_slug = (bank.name if bank else "ledger").replace(" ", "_")
    supplier_slug = supplier.name.replace(" ", "_")
    return send_file(
        output,
        as_attachment=False,
        download_name=f"supplier_ledger_{supplier_slug}_{bank_slug}.pdf",
        mimetype="application/pdf",
    )


@sh_main_bp.route("/order-confirmation", methods=["GET", "POST"])
@login_required
def order_confirmation():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        material_name = request.form.get("material_name", "").strip()
        size = request.form.get("size", "").strip()
        micron = request.form.get("micron", "").strip()
        total_kg = request.form.get("total_kg", type=float)
        client_id = request.form.get("client_company_id", type=int)
        notes = request.form.get("notes", "").strip()

        if not material_name or not total_kg or total_kg <= 0 or not client_id:
            flash("Material name, KG, and purchased-for client are required.", "danger")
            return redirect(url_for("sh_main.order_confirmation"))

        order = ShOrderConfirmation(
            material_name=material_name,
            size=size,
            micron=micron or None,
            total_kg=total_kg,
            client_company_id=client_id,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        ensure_bank_on_create(order)
        db.session.add(order)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShOrderConfirmation",
            order.id,
            f"Order confirmation: {material_name}",
        )
        db.session.commit()
        flash("Order confirmation slip created.", "success")
        return redirect(url_for("sh_main.order_confirmation_pdf", order_id=order.id))

    orders = filter_by_bank(ShOrderConfirmation.query, ShOrderConfirmation).order_by(
        ShOrderConfirmation.created_at.desc()
    ).all()
    return render_template(
        "sh_traders/order_confirmation.html",
        orders=orders,
        clients=clients,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/order-confirmation/<int:order_id>/pdf")
@login_required
def order_confirmation_pdf(order_id):
    order = ShOrderConfirmation.query.get_or_404(order_id)
    bank_id = get_current_sh_bank().id if get_current_sh_bank() else None
    if bank_id and order.bank_id != bank_id:
        abort(404)
    output = generate_order_confirmation_pdf(order)
    return send_file(
        output,
        as_attachment=False,
        download_name=f"order_confirmation_{order_id}.pdf",
        mimetype="application/pdf",
    )


@sh_main_bp.route("/payment-receipt", methods=["GET", "POST"])
@login_required
def payment_receipt():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()

    if request.method == "POST":
        require_edit_access()
        receipt_date = request.form.get("receipt_date")
        client_id = request.form.get("client_company_id", type=int)
        amount_received = request.form.get("amount_received", type=float)
        total_received = request.form.get("total_received", type=float)
        total_due = request.form.get("total_due", type=float)
        sale_invoice_id = request.form.get("sale_invoice_id", type=int) or None
        notes = request.form.get("notes", "").strip()
        receipt_number = request.form.get("receipt_number", "").strip()

        if not receipt_date or not client_id:
            flash("Date and client are required.", "danger")
            return redirect(url_for("sh_main.payment_receipt"))
        if not amount_received or amount_received <= 0:
            flash("Enter a valid amount received on this date.", "danger")
            return redirect(url_for("sh_main.payment_receipt"))
        if total_due is None or total_due < 0:
            flash("Enter a valid total due amount.", "danger")
            return redirect(url_for("sh_main.payment_receipt"))

        if total_received is None or total_received < 0:
            total_received = suggest_total_received(client_id, amount_received)

        if sale_invoice_id:
            invoice = ShSaleInvoice.query.get_or_404(sale_invoice_id)
            if invoice.sold_to_client_id != client_id:
                flash("Selected invoice does not belong to this client.", "danger")
                return redirect(url_for("sh_main.payment_receipt"))
            remaining = get_invoice_remaining(sale_invoice_id)
            if amount_received > remaining + 0.01:
                flash(
                    f"Amount exceeds invoice balance (₨ {remaining:,.2f} remaining on {invoice.invoice_number}).",
                    "danger",
                )
                return redirect(url_for("sh_main.payment_receipt"))

        receipt = ShPaymentReceipt(
            receipt_number=receipt_number or next_payment_receipt_number(),
            receipt_date=_parse_date(receipt_date),
            client_company_id=client_id,
            amount_received=amount_received,
            total_received=total_received,
            total_due=total_due,
            sale_invoice_id=sale_invoice_id,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        ensure_bank_on_create(receipt)
        db.session.add(receipt)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "ShPaymentReceipt",
            receipt.id,
            f"Payment receipt {receipt.receipt_number} for {amount_received:,.2f}",
        )
        db.session.commit()
        flash("Payment receipt created.", "success")
        return redirect(url_for("sh_main.payment_receipt_pdf", receipt_id=receipt.id))

    client_invoice_options = {}
    balance_cache = {}
    for invoice in _bank_sale_invoices().all():
        client_id = invoice.sold_to_client_id
        if client_id not in balance_cache:
            balance_cache[client_id] = compute_invoice_balances(client_id)
        balance = balance_cache[client_id].get(
            invoice.id,
            {"remaining": float(invoice.total_amount or 0), "paid": 0.0},
        )
        client_invoice_options.setdefault(client_id, []).append(
            {
                "id": invoice.id,
                "number": invoice.invoice_number,
                "date": invoice.invoice_date.strftime("%d-%m-%Y"),
                "total": float(invoice.total_amount or 0),
                "remaining": float(balance.get("remaining", 0)),
            }
        )

    return render_template(
        "sh_traders/payment_receipt.html",
        receipts=get_payment_receipts(),
        clients=clients,
        next_receipt_number=next_payment_receipt_number(),
        client_invoice_options=client_invoice_options,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/payment-receipt/<int:receipt_id>/pdf")
@login_required
def payment_receipt_pdf(receipt_id):
    receipt = ShPaymentReceipt.query.get_or_404(receipt_id)
    bank_id = get_current_sh_bank().id if get_current_sh_bank() else None
    if bank_id and receipt.bank_id != bank_id:
        abort(404)
    output = generate_payment_receipt_pdf(receipt)
    return send_file(
        output,
        as_attachment=False,
        download_name=f"payment_receipt_{receipt.receipt_number}.pdf",
        mimetype="application/pdf",
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
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    purchases = _bank_purchases().all()

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
        ensure_bank_on_create(record)
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

    records = filter_by_bank(ShPaymentScreenshot.query, ShPaymentScreenshot).order_by(
        ShPaymentScreenshot.payment_date.desc(), ShPaymentScreenshot.id.desc()
    ).all()
    return render_template(
        "sh_traders/payment_screenshots.html",
        records=records,
        suppliers=suppliers,
        purchases=purchases,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
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
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    invoices = _bank_sale_invoices().all()

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
        ensure_bank_on_create(record)
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

    records = filter_by_bank(ShGatePassScreenshot.query, ShGatePassScreenshot).order_by(
        ShGatePassScreenshot.gate_pass_date.desc(),
        ShGatePassScreenshot.id.desc(),
    ).all()
    return render_template(
        "sh_traders/gate_pass_screenshots.html",
        records=records,
        clients=clients,
        invoices=invoices,
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
    )


@sh_main_bp.route("/gate-passes")
@login_required
def gate_passes_redirect():
    return redirect(url_for("sh_main.sale_invoices"))


@sh_main_bp.route("/sale-invoices", methods=["GET", "POST"])
@login_required
def sale_invoices():
    if not get_current_sh_bank():
        flash("Add a bank first.", "warning")
        return redirect(url_for("sh_main.banks"))

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

        previous_balance, previous_balance_type = resolve_sale_invoice_previous_balance(
            sold_to_id, parsed_date
        )

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
        ensure_bank_on_create(invoice)
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

    invoice_list = _bank_sale_invoices().all()
    balance_cache = {}
    invoice_rows = []
    for invoice in invoice_list:
        client_id = invoice.sold_to_client_id
        if client_id not in balance_cache:
            balance_cache[client_id] = compute_invoice_balances(client_id)
        balance = balance_cache[client_id].get(
            invoice.id,
            {"paid": 0.0, "remaining": float(invoice.total_amount or 0)},
        )
        invoice_rows.append(
            {
                "invoice": invoice,
                "paid": balance.get("paid", 0.0),
                "remaining": balance.get("remaining", float(invoice.total_amount or 0)),
            }
        )

    return render_template(
        "sh_traders/sale_invoices.html",
        invoice_rows=invoice_rows,
        clients=clients,
        next_invoice_number=next_sale_invoice_number(),
        banks=get_all_banks(),
        current_bank=get_current_sh_bank(),
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
