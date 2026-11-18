from datetime import date

from flask import Blueprint, render_template
from flask_login import login_required

from app.models import InventoryTransaction
from app.services.ink_inventory import get_dashboard_stats
from app.services.ink_used_stock import get_used_ink_balances

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def dashboard():
    today = date.today()
    stats = get_dashboard_stats(today)
    today_txns = (
        InventoryTransaction.query.filter_by(transaction_date=today)
        .order_by(InventoryTransaction.id.desc())
        .all()
    )
    used_balances = [row for row in get_used_ink_balances() if row["balance_kg"] > 0.001]
    return render_template(
        "ink/dashboard.html",
        stats=stats,
        today=today,
        today_txns=today_txns,
        used_ink_kg=sum(row["balance_kg"] for row in used_balances),
        used_ink_count=len(used_balances),
    )
