from datetime import date

from flask import Blueprint, current_app, render_template, request
from flask_login import login_required

from app.services.materials_inventory import USAGE_PERIODS, get_dashboard_stats
from app.services.stock_workflow import WORKFLOW_STEPS, has_opening_stock


materials_main_bp = Blueprint("materials_main", __name__, url_prefix="/materials")


@materials_main_bp.route("/")
@login_required
def dashboard():
    usage_period = request.args.get("period", "daily")
    if usage_period not in USAGE_PERIODS:
        usage_period = "daily"

    today = date.today()
    try:
        stats = get_dashboard_stats(today, usage_period=usage_period)
    except Exception:
        current_app.logger.exception("Failed to build materials dashboard stats")
        raise

    return render_template(
        "materials/dashboard.html",
        stats=stats,
        today=today,
        usage_period=usage_period,
        usage_periods=USAGE_PERIODS,
        workflow_steps=WORKFLOW_STEPS,
        has_opening_stock=has_opening_stock("materials"),
    )
