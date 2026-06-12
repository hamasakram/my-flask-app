from datetime import date

from flask import Blueprint, render_template
from flask_login import login_required

from app.services.glue_chemical_inventory import glue_dashboard_stats
from app.services.stock_workflow import WORKFLOW_STEPS, has_opening_stock

glue_main_bp = Blueprint("glue_main", __name__, url_prefix="/glue")


@glue_main_bp.route("/")
@login_required
def dashboard():
    stats = glue_dashboard_stats(date.today())
    return render_template(
        "glue/dashboard.html",
        stats=stats,
        today=date.today(),
        workflow_steps=WORKFLOW_STEPS,
        has_opening_stock=has_opening_stock("glue"),
    )
