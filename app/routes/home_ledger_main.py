from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import HomeLedgerEntry, HomeParty
from app.services.home_ledger import get_dashboard_stats, get_party_balance, get_party_ledger_rows
from app.services.inventory import log_audit

home_ledger_bp = Blueprint("home_ledger", __name__, url_prefix="/home-ledger")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


@home_ledger_bp.route("/")
@login_required
def dashboard():
    stats = get_dashboard_stats()
    return render_template("home_ledger/dashboard.html", stats=stats)


@home_ledger_bp.route("/parties", methods=["GET", "POST"])
@login_required
def parties():
    if request.method == "POST":
        require_edit_access()
        name = request.form.get("party_name", "").strip()
        balance_kind = request.form.get("balance_kind", HomeParty.KIND_TO_PAY)
        opening_amount = request.form.get("opening_amount", type=float) or 0
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Party name is required.", "danger")
            return redirect(url_for("home_ledger.parties"))

        if balance_kind not in (HomeParty.KIND_TO_PAY, HomeParty.KIND_TO_RECEIVE):
            balance_kind = HomeParty.KIND_TO_PAY

        if opening_amount < 0:
            flash("Opening amount cannot be negative.", "danger")
            return redirect(url_for("home_ledger.parties"))

        if HomeParty.query.filter_by(name=name).first():
            flash("This party already exists.", "warning")
            return redirect(url_for("home_ledger.parties"))

        party = HomeParty(
            name=name,
            balance_kind=balance_kind,
            opening_amount=opening_amount,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(party)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "HomeParty",
            party.id,
            f"Home ledger party: {name}",
        )
        db.session.commit()
        flash(f"Party '{name}' added.", "success")
        return redirect(url_for("home_ledger.party_ledger", party_id=party.id))

    party_list = HomeParty.query.order_by(HomeParty.name).all()
    summaries = [
        {"party": p, "balance": get_party_balance(p), "entry_count": p.entries.count()}
        for p in party_list
    ]
    return render_template("home_ledger/parties.html", parties=summaries)


@home_ledger_bp.route("/party/<int:party_id>", methods=["GET", "POST"])
@login_required
def party_ledger(party_id):
    party = HomeParty.query.get_or_404(party_id)

    if request.method == "POST":
        require_edit_access()
        entry_date = request.form.get("entry_date")
        given = request.form.get("given", type=float) or 0
        received = request.form.get("received", type=float) or 0
        notes = request.form.get("notes", "").strip()

        if not entry_date:
            flash("Entry date is required.", "danger")
            return redirect(url_for("home_ledger.party_ledger", party_id=party_id))

        if given <= 0 and received <= 0:
            flash("Enter a given or received amount.", "danger")
            return redirect(url_for("home_ledger.party_ledger", party_id=party_id))

        if given > 0 and received > 0:
            flash("Enter either given or received, not both.", "danger")
            return redirect(url_for("home_ledger.party_ledger", party_id=party_id))

        entry = HomeLedgerEntry(
            party_id=party.id,
            entry_date=_parse_date(entry_date),
            given=given,
            received=received,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(entry)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "HomeLedgerEntry",
            entry.id,
            f"Home ledger entry for {party.name}",
        )
        db.session.commit()
        flash("Ledger entry added.", "success")
        return redirect(url_for("home_ledger.party_ledger", party_id=party_id))

    ledger_rows = get_party_ledger_rows(party)
    current_balance = get_party_balance(party)
    return render_template(
        "home_ledger/party_ledger.html",
        party=party,
        ledger_rows=ledger_rows,
        current_balance=current_balance,
    )
