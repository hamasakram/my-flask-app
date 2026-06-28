from datetime import datetime

from flask import Blueprint, make_response, render_template, request, send_file
from flask_login import login_required
from sqlalchemy import extract

from app.models import AuditLog, Company, InkType, InventoryTransaction
from app.services.companies import get_ink_companies
from app.services.export import export_inventory_excel, export_inventory_pdf, export_transactions_excel
from app.services.inventory import calculate_live_stock

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _parse_filters():
    company_id = request.args.get("company_id", type=int)
    ink_type_id = request.args.get("ink_type_id", type=int)
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
    return company_id, ink_type_id, parsed_start, parsed_end


@reports_bp.route("/transactions")
@login_required
def transactions():
    company_id, ink_type_id, start_date, end_date = _parse_filters()

    query = InventoryTransaction.query.join(Company).join(InkType)
    if company_id:
        query = query.filter(InventoryTransaction.company_id == company_id)
    if ink_type_id:
        query = query.filter(InventoryTransaction.ink_type_id == ink_type_id)
    if start_date:
        query = query.filter(InventoryTransaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(InventoryTransaction.transaction_date <= end_date)

    transactions_list = query.order_by(
        InventoryTransaction.transaction_date.desc(),
        InventoryTransaction.id.desc(),
    ).all()

    companies = get_ink_companies()
    inks = InkType.query.order_by(InkType.name).all()

    return render_template(
        "transactions.html",
        transactions=transactions_list,
        companies=companies,
        inks=inks,
        filters={
            "company_id": company_id,
            "ink_type_id": ink_type_id,
            "start_date": request.args.get("start_date", ""),
            "end_date": request.args.get("end_date", ""),
        },
    )


@reports_bp.route("/company")
@login_required
def company_report():
    company_id = request.args.get("company_id", type=int)
    rows = calculate_live_stock(company_id=company_id)
    companies = get_ink_companies()
    return render_template(
        "company_report.html",
        rows=rows,
        companies=companies,
        selected_company=company_id,
    )


@reports_bp.route("/ink")
@login_required
def ink_report():
    ink_search = request.args.get("ink", "").strip().lower()
    rows = calculate_live_stock()
    if ink_search:
        rows = [r for r in rows if ink_search in r["ink_type"].name.lower()]
    return render_template("ink_report.html", rows=rows, ink_search=request.args.get("ink", ""))


@reports_bp.route("/monthly")
@login_required
def monthly_report():
    month = request.args.get("month", type=int) or datetime.now().month
    year = request.args.get("year", type=int) or datetime.now().year
    company_id = request.args.get("company_id", type=int)

    query = InventoryTransaction.query.filter(
        extract("month", InventoryTransaction.transaction_date) == month,
        extract("year", InventoryTransaction.transaction_date) == year,
    )
    if company_id:
        query = query.filter(InventoryTransaction.company_id == company_id)

    txns = query.order_by(InventoryTransaction.transaction_date).all()
    live_rows = calculate_live_stock(company_id=company_id)
    companies = get_ink_companies()

    received = sum(t.quantity for t in txns if t.transaction_type == InventoryTransaction.TRANSACTION_RECEIVED)
    issued = sum(t.quantity for t in txns if t.transaction_type == InventoryTransaction.TRANSACTION_ISSUED)
    used = sum(t.quantity for t in txns if t.transaction_type == InventoryTransaction.TRANSACTION_USED)

    return render_template(
        "period_report.html",
        period_label=f"{datetime(year, month, 1).strftime('%B %Y')}",
        txns=txns,
        live_rows=live_rows,
        companies=companies,
        selected_company=company_id,
        received=received,
        issued=issued,
        used=used,
        period_type="monthly",
        month=month,
        year=year,
    )


@reports_bp.route("/yearly")
@login_required
def yearly_report():
    year = request.args.get("year", type=int) or datetime.now().year
    company_id = request.args.get("company_id", type=int)

    query = InventoryTransaction.query.filter(
        extract("year", InventoryTransaction.transaction_date) == year
    )
    if company_id:
        query = query.filter(InventoryTransaction.company_id == company_id)

    txns = query.order_by(InventoryTransaction.transaction_date).all()
    live_rows = calculate_live_stock(company_id=company_id)
    companies = get_ink_companies()

    received = sum(t.quantity for t in txns if t.transaction_type == InventoryTransaction.TRANSACTION_RECEIVED)
    issued = sum(t.quantity for t in txns if t.transaction_type == InventoryTransaction.TRANSACTION_ISSUED)
    used = sum(t.quantity for t in txns if t.transaction_type == InventoryTransaction.TRANSACTION_USED)

    return render_template(
        "period_report.html",
        period_label=str(year),
        txns=txns,
        live_rows=live_rows,
        companies=companies,
        selected_company=company_id,
        received=received,
        issued=issued,
        used=used,
        period_type="yearly",
        year=year,
    )


@reports_bp.route("/audit")
@login_required
def audit_trail():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    query = AuditLog.query
    if start_date:
        query = query.filter(
            AuditLog.created_at >= datetime.strptime(start_date, "%Y-%m-%d")
        )
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        query = query.filter(AuditLog.created_at <= end_dt)

    logs = query.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template(
        "audit_trail.html",
        logs=logs,
        filters={"start_date": start_date or "", "end_date": end_date or ""},
    )


@reports_bp.route("/export/inventory/<fmt>")
@login_required
def export_inventory(fmt):
    company_id = request.args.get("company_id", type=int)
    rows = calculate_live_stock(company_id=company_id)

    if fmt == "excel":
        output = export_inventory_excel(rows)
        response = make_response(output.read())
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        response.headers["Content-Disposition"] = "attachment; filename=inventory_report.xlsx"
        return response

    if fmt == "pdf":
        output = export_inventory_pdf(rows)
        return send_file(output, as_attachment=True, download_name="inventory_report.pdf", mimetype="application/pdf")

    return "Invalid format", 400


@reports_bp.route("/export/transactions/<fmt>")
@login_required
def export_transactions(fmt):
    company_id, ink_type_id, start_date, end_date = _parse_filters()

    query = InventoryTransaction.query
    if company_id:
        query = query.filter(InventoryTransaction.company_id == company_id)
    if ink_type_id:
        query = query.filter(InventoryTransaction.ink_type_id == ink_type_id)
    if start_date:
        query = query.filter(InventoryTransaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(InventoryTransaction.transaction_date <= end_date)

    txns = query.order_by(InventoryTransaction.transaction_date.desc()).all()

    if fmt == "excel":
        output = export_transactions_excel(txns)
        response = make_response(output.read())
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        response.headers["Content-Disposition"] = "attachment; filename=transactions_report.xlsx"
        return response

    if fmt == "pdf":
        rows = []
        for txn in txns:
            rows.append(
                {
                    "company": txn.company,
                    "ink_type": txn.ink_type,
                    "opening": 0,
                    "received": txn.quantity if txn.transaction_type == InventoryTransaction.TRANSACTION_RECEIVED else 0,
                    "used": txn.quantity if txn.transaction_type == InventoryTransaction.TRANSACTION_USED else 0,
                    "current": txn.quantity,
                    "threshold": 0,
                    "is_low": False,
                }
            )
        output = export_inventory_pdf(rows, title="Transaction Report")
        return send_file(output, as_attachment=True, download_name="transactions_report.pdf", mimetype="application/pdf")

    return "Invalid format", 400
