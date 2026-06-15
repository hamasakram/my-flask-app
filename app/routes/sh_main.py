from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (
    ShClientCompany,
    ShLedgerEntry,
    ShOpeningBalance,
    ShPurchase,
    ShSupplierCompany,
)
from app.services.inventory import log_audit
from app.services.sh_traders import (
    calculate_total_amount,
    get_current_ledger_balance,
    get_dashboard_stats,
    get_ledger_rows,
    get_opening_balance,
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
    return render_template(
        "sh_traders/payments.html",
        opening=opening,
        ledger_rows=ledger_rows,
        current_balance=get_current_ledger_balance(),
    )
