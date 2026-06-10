from datetime import date

from flask import Blueprint, render_template
from flask_login import login_required

from app.services.materials_inventory import get_dashboard_stats
from app.services.stock_workflow import WORKFLOW_STEPS, has_opening_stock


materials_main_bp = Blueprint("materials_main", __name__, url_prefix="/materials")


@materials_main_bp.route("/")
@login_required
def dashboard():
    stats = get_dashboard_stats(date.today())
    return render_template(
        "materials/dashboard.html",
        stats=stats,
        today=date.today(),
        workflow_steps=WORKFLOW_STEPS,
        has_opening_stock=has_opening_stock("materials"),
    )
