from datetime import datetime

from flask import Blueprint, make_response, render_template, request, send_file
from flask_login import login_required
from sqlalchemy import extract

from app.models import AuditLog, Company, Material, MaterialTransaction
from app.services.materials_export import (
    export_material_inventory_excel,
    export_material_inventory_pdf,
    export_material_transactions_excel,
)
from app.services.materials_inventory import calculate_live_stock

materials_reports_bp = Blueprint("materials_reports", __name__, url_prefix="/materials/reports")


def _parse_filters():
    company_id = request.args.get("company_id", type=int)
    material_id = request.args.get("material_id", type=int)
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
    return company_id, material_id, parsed_start, parsed_end


@materials_reports_bp.route("/transactions")
@login_required
def transactions():
    company_id, material_id, start_date, end_date = _parse_filters()

    query = MaterialTransaction.query.join(Company).join(Material)
    if company_id:
        query = query.filter(MaterialTransaction.company_id == company_id)
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

    companies = Company.query.filter_by(is_active=True).order_by(Company.name).all()
    materials = Material.query.order_by(Material.name).all()

    return render_template(
        "materials/transactions.html",
        transactions=transactions_list,
        companies=companies,
        materials=materials,
        filters={
            "company_id": company_id,
            "material_id": material_id,
            "start_date": request.args.get("start_date", ""),
            "end_date": request.args.get("end_date", ""),
        },
    )


@materials_reports_bp.route("/company")
@login_required
def company_report():
    company_id = request.args.get("company_id", type=int)
    rows = calculate_live_stock(company_id=company_id)
    companies = Company.query.filter_by(is_active=True).order_by(Company.name).all()
    return render_template(
        "materials/company_report.html",
        rows=rows,
        companies=companies,
        selected_company=company_id,
    )


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
    company_id = request.args.get("company_id", type=int)

    query = MaterialTransaction.query.filter(
        extract("month", MaterialTransaction.transaction_date) == month,
        extract("year", MaterialTransaction.transaction_date) == year,
    )
    if company_id:
        query = query.filter(MaterialTransaction.company_id == company_id)

    txns = query.order_by(MaterialTransaction.transaction_date).all()
    live_rows = calculate_live_stock(company_id=company_id)
    companies = Company.query.filter_by(is_active=True).order_by(Company.name).all()

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
        companies=companies,
        selected_company=company_id,
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
    company_id = request.args.get("company_id", type=int)

    query = MaterialTransaction.query.filter(
        extract("year", MaterialTransaction.transaction_date) == year
    )
    if company_id:
        query = query.filter(MaterialTransaction.company_id == company_id)

    txns = query.order_by(MaterialTransaction.transaction_date).all()
    live_rows = calculate_live_stock(company_id=company_id)
    companies = Company.query.filter_by(is_active=True).order_by(Company.name).all()

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
        companies=companies,
        selected_company=company_id,
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


@materials_reports_bp.route("/export/inventory/<fmt>")
@login_required
def export_inventory(fmt):
    company_id = request.args.get("company_id", type=int)
    rows = calculate_live_stock(company_id=company_id)

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
    company_id, material_id, start_date, end_date = _parse_filters()

    query = MaterialTransaction.query
    if company_id:
        query = query.filter(MaterialTransaction.company_id == company_id)
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
                    "company": txn.company,
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
