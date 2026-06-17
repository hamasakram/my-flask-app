from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import BankAccount, BankLedgerEntry
from app.services.bank_ledger import (
    bank_account_exists,
    get_bank_balance,
    get_bank_ledger_rows,
    get_dashboard_stats,
)
from app.services.inventory import log_audit

bank_ledger_bp = Blueprint("bank_ledger", __name__, url_prefix="/bank-ledger")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


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

    bank_list = BankAccount.query.order_by(BankAccount.bank_name, BankAccount.account_number).all()
    summaries = [
        {"bank": b, "balance": get_bank_balance(b), "entry_count": b.entries.count()}
        for b in bank_list
    ]
    return render_template("bank_ledger/banks.html", banks=summaries)


@bank_ledger_bp.route("/bank/<int:bank_id>", methods=["GET", "POST"])
@login_required
def bank_ledger(bank_id):
    bank = BankAccount.query.get_or_404(bank_id)

    if request.method == "POST":
        require_edit_access()
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
    )
