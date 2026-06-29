from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import db
from app.models import BankAccount, BankLedgerEntry, BankTransfer
from app.services.bank_ledger import (
    bank_account_exists,
    create_bank_transfer,
    get_bank_balance,
    get_bank_ledger_rows,
    get_dashboard_stats,
    get_rokar_day_data,
)
from app.services.bank_rokar_pdf import generate_rokar_pdf
from app.services.inventory import log_audit

bank_ledger_bp = Blueprint("bank_ledger", __name__, url_prefix="/bank-ledger")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _all_banks():
    return BankAccount.query.order_by(BankAccount.bank_name, BankAccount.account_number).all()


@bank_ledger_bp.route("/")
@login_required
def dashboard():
    stats = get_dashboard_stats()
    return render_template("bank_ledger/dashboard.html", stats=stats)


@bank_ledger_bp.route("/banks", methods=["GET", "POST"])
@login_required
def banks():
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
            return redirect(url_for("bank_ledger.banks"))

        if opening_balance < 0:
            flash("Opening balance cannot be negative.", "danger")
            return redirect(url_for("bank_ledger.banks"))

        if bank_account_exists(bank_name, account_number or None):
            flash("This bank account already exists.", "warning")
            return redirect(url_for("bank_ledger.banks"))

        bank = BankAccount(
            bank_name=bank_name,
            account_title=account_title or None,
            account_number=account_number or None,
            branch=branch or None,
            opening_balance=opening_balance,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(bank)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "BankAccount",
            bank.id,
            f"Bank account: {bank.display_name}",
        )
        db.session.commit()
        flash(f"Bank '{bank.display_name}' added.", "success")
        return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank.id))

    bank_list = _all_banks()
    summaries = [
        {"bank": b, "balance": get_bank_balance(b), "entry_count": b.entries.count()}
        for b in bank_list
    ]
    return render_template("bank_ledger/banks.html", banks=summaries)


@bank_ledger_bp.route("/transfers", methods=["GET", "POST"])
@login_required
def transfers():
    all_banks = _all_banks()

    if request.method == "POST":
        require_edit_access()
        if len(all_banks) < 2:
            flash("Add at least two bank accounts before recording transfers.", "danger")
            return redirect(url_for("bank_ledger.banks"))

        transfer_date = request.form.get("transfer_date")
        from_bank_id = request.form.get("from_bank_id", type=int)
        to_bank_id = request.form.get("to_bank_id", type=int)
        amount = request.form.get("amount", type=float)
        reference = request.form.get("reference", "").strip()
        notes = request.form.get("notes", "").strip()

        if not transfer_date or not from_bank_id or not to_bank_id or not amount:
            flash("Date, from bank, to bank, and amount are required.", "danger")
            return redirect(url_for("bank_ledger.transfers"))

        try:
            transfer = create_bank_transfer(
                from_bank_id=from_bank_id,
                to_bank_id=to_bank_id,
                transfer_date=_parse_date(transfer_date),
                amount=amount,
                reference=reference or None,
                notes=notes or None,
                created_by_id=current_user.id,
            )
            db.session.flush()
            log_audit(
                current_user.id,
                "CREATE",
                "BankTransfer",
                transfer.id,
                f"Transfer {amount:,.2f} from #{from_bank_id} to #{to_bank_id}",
            )
            db.session.commit()
            flash(
                f"Transfer recorded — {transfer.from_bank.display_name} → "
                f"{transfer.to_bank.display_name} (₨ {amount:,.2f}). Both ledgers updated.",
                "success",
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("bank_ledger.transfers"))

        return redirect(url_for("bank_ledger.transfers"))

    transfer_list = (
        BankTransfer.query.order_by(
            BankTransfer.transfer_date.desc(), BankTransfer.id.desc()
        ).all()
    )
    return render_template(
        "bank_ledger/transfers.html",
        transfers=transfer_list,
        banks=all_banks,
    )


@bank_ledger_bp.route("/bank/<int:bank_id>", methods=["GET", "POST"])
@login_required
def bank_ledger(bank_id):
    bank = BankAccount.query.get_or_404(bank_id)
    all_banks = _all_banks()
    other_banks = [b for b in all_banks if b.id != bank.id]

    if request.method == "POST":
        require_edit_access()
        action = request.form.get("action", "entry")

        if action == "transfer":
            if len(all_banks) < 2:
                flash("Add another bank account to record cross-bank transfers.", "danger")
                return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank_id))

            transfer_date = request.form.get("transfer_date")
            to_bank_id = request.form.get("to_bank_id", type=int)
            amount = request.form.get("amount", type=float)
            reference = request.form.get("reference", "").strip()
            notes = request.form.get("notes", "").strip()

            if not transfer_date or not to_bank_id or not amount:
                flash("Date, destination bank, and amount are required.", "danger")
                return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank_id))

            try:
                transfer = create_bank_transfer(
                    from_bank_id=bank.id,
                    to_bank_id=to_bank_id,
                    transfer_date=_parse_date(transfer_date),
                    amount=amount,
                    reference=reference or None,
                    notes=notes or None,
                    created_by_id=current_user.id,
                )
                db.session.flush()
                log_audit(
                    current_user.id,
                    "CREATE",
                    "BankTransfer",
                    transfer.id,
                    f"Transfer from {bank.display_name} to #{to_bank_id}",
                )
                db.session.commit()
                flash(
                    f"Transfer sent to {transfer.to_bank.display_name} — "
                    f"both bank balances updated.",
                    "success",
                )
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank_id))

        entry_date = request.form.get("entry_date")
        deposit = request.form.get("deposit", type=float) or 0
        withdrawal = request.form.get("withdrawal", type=float) or 0
        notes = request.form.get("notes", "").strip()

        if not entry_date:
            flash("Entry date is required.", "danger")
            return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank_id))

        if deposit <= 0 and withdrawal <= 0:
            flash("Enter a deposit or withdrawal amount.", "danger")
            return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank_id))

        if deposit > 0 and withdrawal > 0:
            flash("Enter either deposit or withdrawal, not both.", "danger")
            return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank_id))

        entry = BankLedgerEntry(
            bank_id=bank.id,
            entry_date=_parse_date(entry_date),
            deposit=deposit,
            withdrawal=withdrawal,
            entry_type=BankLedgerEntry.TYPE_STANDARD,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(entry)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "BankLedgerEntry",
            entry.id,
            f"Bank ledger entry for {bank.display_name}",
        )
        db.session.commit()
        flash("Ledger entry added.", "success")
        return redirect(url_for("bank_ledger.bank_ledger", bank_id=bank_id))

    ledger_rows = get_bank_ledger_rows(bank)
    current_balance = get_bank_balance(bank)
    return render_template(
        "bank_ledger/bank_ledger.html",
        bank=bank,
        ledger_rows=ledger_rows,
        current_balance=current_balance,
        other_banks=other_banks,
    )


@bank_ledger_bp.route("/rokar", methods=["GET", "POST"])
@login_required
def rokar():
    all_banks = _all_banks()
    entry_date_raw = request.args.get("date") or request.form.get("entry_date")
    if entry_date_raw:
        try:
            selected_date = _parse_date(entry_date_raw)
        except ValueError:
            flash("Invalid date.", "danger")
            selected_date = date.today()
    else:
        selected_date = date.today()

    if request.method == "POST":
        require_edit_access()
        action = request.form.get("action", "entry")

        if action == "transfer":
            if len(all_banks) < 2:
                flash("Add at least two bank accounts before recording transfers.", "danger")
                return redirect(url_for("bank_ledger.rokar", date=selected_date.isoformat()))

            transfer_date = request.form.get("transfer_date")
            from_bank_id = request.form.get("from_bank_id", type=int)
            to_bank_id = request.form.get("to_bank_id", type=int)
            amount = request.form.get("amount", type=float)
            reference = request.form.get("reference", "").strip()
            notes = request.form.get("notes", "").strip()

            if not transfer_date or not from_bank_id or not to_bank_id or not amount:
                flash("Date, from bank, to bank, and amount are required.", "danger")
                return redirect(url_for("bank_ledger.rokar", date=selected_date.isoformat()))

            try:
                transfer = create_bank_transfer(
                    from_bank_id=from_bank_id,
                    to_bank_id=to_bank_id,
                    transfer_date=_parse_date(transfer_date),
                    amount=amount,
                    reference=reference or None,
                    notes=notes or None,
                    created_by_id=current_user.id,
                )
                db.session.flush()
                log_audit(
                    current_user.id,
                    "CREATE",
                    "BankTransfer",
                    transfer.id,
                    f"Rokar transfer {amount:,.2f} from #{from_bank_id} to #{to_bank_id}",
                )
                db.session.commit()
                flash("Cross-bank transfer recorded in daily rokar.", "success")
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            return redirect(url_for("bank_ledger.rokar", date=_parse_date(transfer_date).isoformat()))

        bank_id = request.form.get("bank_id", type=int)
        entry_date = request.form.get("entry_date")
        deposit = request.form.get("deposit", type=float) or 0
        withdrawal = request.form.get("withdrawal", type=float) or 0
        notes = request.form.get("notes", "").strip()

        if not bank_id or not entry_date:
            flash("Bank and date are required.", "danger")
            return redirect(url_for("bank_ledger.rokar", date=selected_date.isoformat()))

        if deposit <= 0 and withdrawal <= 0:
            flash("Enter a deposit or withdrawal amount.", "danger")
            return redirect(url_for("bank_ledger.rokar", date=selected_date.isoformat()))

        if deposit > 0 and withdrawal > 0:
            flash("Enter either deposit or withdrawal, not both.", "danger")
            return redirect(url_for("bank_ledger.rokar", date=selected_date.isoformat()))

        bank = BankAccount.query.get(bank_id)
        if not bank:
            flash("Invalid bank account.", "danger")
            return redirect(url_for("bank_ledger.rokar", date=selected_date.isoformat()))

        parsed_date = _parse_date(entry_date)
        entry = BankLedgerEntry(
            bank_id=bank.id,
            entry_date=parsed_date,
            deposit=deposit,
            withdrawal=withdrawal,
            entry_type=BankLedgerEntry.TYPE_STANDARD,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(entry)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "BankLedgerEntry",
            entry.id,
            f"Rokar entry for {bank.display_name}",
        )
        db.session.commit()
        flash("Daily rokar entry added.", "success")
        return redirect(url_for("bank_ledger.rokar", date=parsed_date.isoformat()))

    rokar_data = get_rokar_day_data(selected_date)
    return render_template(
        "bank_ledger/rokar.html",
        banks=all_banks,
        rokar=rokar_data,
        selected_date=selected_date,
    )


@bank_ledger_bp.route("/rokar/pdf")
@login_required
def rokar_pdf():
    entry_date_raw = request.args.get("date")
    if not entry_date_raw:
        abort(400)
    try:
        entry_date = _parse_date(entry_date_raw)
    except ValueError:
        abort(400)

    output = generate_rokar_pdf(entry_date)
    filename = f"rokar_roznamcha_{entry_date.strftime('%Y%m%d')}.pdf"
    return send_file(
        output,
        as_attachment=False,
        download_name=filename,
        mimetype="application/pdf",
    )
