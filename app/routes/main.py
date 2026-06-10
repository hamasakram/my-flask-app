from datetime import date

from flask import Blueprint, render_template
from flask_login import login_required

from app.services.inventory import get_dashboard_stats
from app.services.stock_workflow import WORKFLOW_STEPS, has_opening_stock


main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def dashboard():
    stats = get_dashboard_stats(date.today())
    return render_template(
        "dashboard.html",
        stats=stats,
        today=date.today(),
        workflow_steps=WORKFLOW_STEPS,
        has_opening_stock=has_opening_stock("ink"),
    )
