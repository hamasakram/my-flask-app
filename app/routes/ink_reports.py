from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, send_file
from flask_login import login_required

from app.services.ink_export import export_ink_daily_pdf
from app.services.ink_inventory import get_daily_report_data

ink_reports_bp = Blueprint("ink_reports", __name__, url_prefix="/reports")


@ink_reports_bp.route("/daily")
@login_required
def daily_report():
    report_date_str = request.args.get("date")
    if report_date_str:
        report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
    else:
        report_date = date.today()

    report_data = get_daily_report_data(report_date)
    day_in_kg = sum(
        float(t.quantity or 0) * float(t.ink_type.weight_per_can or 0)
        for t in report_data["cans_in_today"]
    )
    day_out_kg = sum(
        float(t.quantity or 0) * float(t.ink_type.weight_per_can or 0)
        for t in report_data["cans_out_today"]
    )
    day_txns = sorted(
        list(report_data["cans_in_today"]) + list(report_data["cans_out_today"]),
        key=lambda t: t.id,
    )
    return render_template(
        "ink/daily_report.html",
        report_data=report_data,
        selected_date=report_date.strftime("%Y-%m-%d"),
        prev_date=(report_date - timedelta(days=1)).isoformat(),
        next_date=(report_date + timedelta(days=1)).isoformat(),
        date_in_url=bool(report_date_str),
        day_in_kg=day_in_kg,
        day_out_kg=day_out_kg,
        day_txns=day_txns,
    )


@ink_reports_bp.route("/daily/pdf")
@login_required
def daily_report_pdf():
    report_date_str = request.args.get("date")
    if report_date_str:
        report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
    else:
        report_date = date.today()

    report_data = get_daily_report_data(report_date)
    output = export_ink_daily_pdf(report_data)
    filename = f"ink_daily_report_{report_date.strftime('%Y-%m-%d')}.pdf"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )
