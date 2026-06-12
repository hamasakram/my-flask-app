from datetime import date

from flask import Blueprint, render_template
from flask_login import login_required

from app.services.glue_chemical_inventory import chemical_dashboard_stats
from app.services.stock_workflow import WORKFLOW_STEPS, has_opening_stock

chemicals_main_bp = Blueprint("chemicals_main", __name__, url_prefix="/chemicals")


@chemicals_main_bp.route("/")
@login_required
def dashboard():
    stats = chemical_dashboard_stats(date.today())
    return render_template(
        "chemicals/dashboard.html",
        stats=stats,
        today=date.today(),
        workflow_steps=WORKFLOW_STEPS,
        has_opening_stock=has_opening_stock("chemicals"),
    )
