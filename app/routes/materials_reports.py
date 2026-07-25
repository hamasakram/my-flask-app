from datetime import date, datetime

from flask import Blueprint, render_template, request, send_file
from flask_login import login_required

from app.services.materials_export import export_materials_daily_pdf
from app.services.materials_inventory import get_daily_report_data

materials_reports_bp = Blueprint("materials_reports", __name__, url_prefix="/materials/reports")


@materials_reports_bp.route("/daily")
@login_required
def daily_report():
    report_date_str = request.args.get("date")
    if report_date_str:
        report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
    else:
        report_date = date.today()

    report_data = get_daily_report_data(report_date)
    return render_template(
        "materials/daily_report.html",
        report_data=report_data,
        selected_date=report_date.strftime("%Y-%m-%d"),
    )


@materials_reports_bp.route("/daily/pdf")
@login_required
def daily_report_pdf():
    report_date_str = request.args.get("date")
    if report_date_str:
        report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
    else:
        report_date = date.today()

    report_data = get_daily_report_data(report_date)
    output = export_materials_daily_pdf(report_data)
    filename = f"materials_daily_report_{report_date.strftime('%Y-%m-%d')}.pdf"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )
