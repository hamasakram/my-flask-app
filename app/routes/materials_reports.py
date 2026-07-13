from datetime import datetime

from flask import Blueprint, make_response, redirect, render_template, request, send_file, url_for
from flask_login import login_required
from sqlalchemy import extract

from app.models import AuditLog, Material, MaterialTransaction
from app.services.materials_export import (
    export_material_inventory_excel,
    export_material_inventory_pdf,
    export_material_transactions_excel,
    export_material_usage_pdf,
)
from app.services.materials_inventory import (
    USAGE_PERIODS,
    calculate_live_stock,
    get_materials_in_opening_stock,
    get_usage_report,
)

materials_reports_bp = Blueprint("materials_reports", __name__, url_prefix="/materials/reports")


def _parse_filters():
    material_id = request.args.get("material_id", type=int)
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
    return material_id, parsed_start, parsed_end


@materials_reports_bp.route("/transactions")
@login_required
def transactions():
    material_id, start_date, end_date = _parse_filters()

    query = MaterialTransaction.query.join(Material)
    if material_id:
        query = query.filter(MaterialTransaction.material_id == material_id)
    if start_date:
        query = query.filter(MaterialTransaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(MaterialTransaction.transaction_date <= end_date)

    transactions_list = query.order_by(
        MaterialTransaction.transaction_date.desc(),
        MaterialTransaction.id.desc(),
    ).all()

    materials = get_materials_in_opening_stock()

    return render_template(
        "materials/transactions.html",
        transactions=transactions_list,
        materials=materials,
        filters={
            "material_id": material_id,
            "start_date": request.args.get("start_date", ""),
            "end_date": request.args.get("end_date", ""),
        },
    )


@materials_reports_bp.route("/company")
@login_required
def company_report():
    flash("Company reports are not available for Materials.", "info")
    return redirect(url_for("materials_reports.material_report"))


@materials_reports_bp.route("/material")
@login_required
def material_report():
    material_search = request.args.get("material", "").strip().lower()
    rows = calculate_live_stock()
    if material_search:
        rows = [
            r for r in rows if material_search in r["material"].display_name.lower()
        ]
    return render_template(
        "materials/material_report.html",
        rows=rows,
        material_search=request.args.get("material", ""),
    )


@materials_reports_bp.route("/monthly")
@login_required
def monthly_report():
    month = request.args.get("month", type=int) or datetime.now().month
    year = request.args.get("year", type=int) or datetime.now().year

    query = MaterialTransaction.query.filter(
        extract("month", MaterialTransaction.transaction_date) == month,
        extract("year", MaterialTransaction.transaction_date) == year,
    )

    txns = query.order_by(MaterialTransaction.transaction_date).all()
    live_rows = calculate_live_stock()

    received = sum(
        t.quantity for t in txns if t.transaction_type == MaterialTransaction.TRANSACTION_RECEIVED
    )
    used = sum(
        t.quantity for t in txns if t.transaction_type == MaterialTransaction.TRANSACTION_USED
    )

    return render_template(
        "materials/period_report.html",
        period_label=f"{datetime(year, month, 1).strftime('%B %Y')}",
        txns=txns,
        live_rows=live_rows,
        received=received,
        used=used,
        period_type="monthly",
        month=month,
        year=year,
    )


@materials_reports_bp.route("/yearly")
@login_required
def yearly_report():
    year = request.args.get("year", type=int) or datetime.now().year

    query = MaterialTransaction.query.filter(
        extract("year", MaterialTransaction.transaction_date) == year
    )

    txns = query.order_by(MaterialTransaction.transaction_date).all()
    live_rows = calculate_live_stock()

    received = sum(
        t.quantity for t in txns if t.transaction_type == MaterialTransaction.TRANSACTION_RECEIVED
    )
    used = sum(
        t.quantity for t in txns if t.transaction_type == MaterialTransaction.TRANSACTION_USED
    )

    return render_template(
        "materials/period_report.html",
        period_label=str(year),
        txns=txns,
        live_rows=live_rows,
        received=received,
        used=used,
        period_type="yearly",
        year=year,
    )


@materials_reports_bp.route("/audit")
@login_required
def audit_trail():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    query = AuditLog.query
    if start_date:
        query = query.filter(AuditLog.created_at >= datetime.strptime(start_date, "%Y-%m-%d"))
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        query = query.filter(AuditLog.created_at <= end_dt)

    logs = query.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template(
        "materials/audit_trail.html",
        logs=logs,
        filters={"start_date": start_date or "", "end_date": end_date or ""},
    )


@materials_reports_bp.route("/usage/pdf")
@login_required
def export_usage_pdf():
    period = request.args.get("period", "daily")
    if period not in USAGE_PERIODS:
        period = "daily"

    reference = None
    date_param = request.args.get("date", "").strip()
    if date_param:
        try:
            reference = datetime.strptime(date_param, "%Y-%m-%d").date()
        except ValueError:
            pass

    report = get_usage_report(period, reference)
    output = export_material_usage_pdf(report)
    filename = f"materials_usage_{period}_{report['start_date'].strftime('%Y%m%d')}.pdf"
    if report["start_date"] != report["end_date"]:
        filename = (
            f"materials_usage_{period}_"
            f"{report['start_date'].strftime('%Y%m%d')}_{report['end_date'].strftime('%Y%m%d')}.pdf"
        )

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


@materials_reports_bp.route("/export/inventory/<fmt>")
@login_required
def export_inventory(fmt):
    rows = calculate_live_stock()

    if fmt == "excel":
        output = export_material_inventory_excel(rows)
        response = make_response(output.read())
        response.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response.headers["Content-Disposition"] = (
            "attachment; filename=materials_inventory_report.xlsx"
        )
        return response

    if fmt == "pdf":
        output = export_material_inventory_pdf(rows)
        return send_file(
            output,
            as_attachment=True,
            download_name="materials_inventory_report.pdf",
            mimetype="application/pdf",
        )

    return "Invalid format", 400


@materials_reports_bp.route("/export/transactions/<fmt>")
@login_required
def export_transactions(fmt):
    material_id, start_date, end_date = _parse_filters()

    query = MaterialTransaction.query
    if material_id:
        query = query.filter(MaterialTransaction.material_id == material_id)
    if start_date:
        query = query.filter(MaterialTransaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(MaterialTransaction.transaction_date <= end_date)

    txns = query.order_by(MaterialTransaction.transaction_date.desc()).all()

    if fmt == "excel":
        output = export_material_transactions_excel(txns)
        response = make_response(output.read())
        response.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response.headers["Content-Disposition"] = (
            "attachment; filename=materials_transactions_report.xlsx"
        )
        return response

    if fmt == "pdf":
        rows = []
        for txn in txns:
            rows.append(
                {
                    "material": txn.material,
                    "opening": 0,
                    "received": txn.quantity
                    if txn.transaction_type == MaterialTransaction.TRANSACTION_RECEIVED
                    else 0,
                    "used": txn.quantity
                    if txn.transaction_type == MaterialTransaction.TRANSACTION_USED
                    else 0,
                    "current": txn.quantity,
                    "threshold": 0,
                    "is_low": False,
                }
            )
        output = export_material_inventory_pdf(rows, title="Materials Transaction Report")
        return send_file(
            output,
            as_attachment=True,
            download_name="materials_transactions_report.pdf",
            mimetype="application/pdf",
        )

    return "Invalid format", 400
