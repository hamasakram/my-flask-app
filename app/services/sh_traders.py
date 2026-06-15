from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import ShClientCompany, ShLedgerEntry, ShOpeningBalance, ShPurchase, ShSupplierCompany


def calculate_total_amount(total_kg: float, rate_per_1000_kg: float) -> float:
    """Total = (kg / 1000) × rate per 1000 kg."""
    if not total_kg or not rate_per_1000_kg:
        return 0.0
    return (float(total_kg) / 1000.0) * float(rate_per_1000_kg)


def get_opening_balance() -> Optional[ShOpeningBalance]:
    return ShOpeningBalance.query.order_by(ShOpeningBalance.id.asc()).first()


def get_ledger_rows() -> list[dict]:
    opening = get_opening_balance()
    balance = float(opening.amount) if opening else 0.0
    entries = ShLedgerEntry.query.order_by(
        ShLedgerEntry.entry_date.asc(), ShLedgerEntry.id.asc()
    ).all()
    rows = []
    for entry in entries:
        balance += float(entry.credit or 0) - float(entry.debit or 0)
        rows.append({"entry": entry, "balance": balance})
    return rows


def get_current_ledger_balance() -> float:
    rows = get_ledger_rows()
    if rows:
        return rows[-1]["balance"]
    opening = get_opening_balance()
    return float(opening.amount) if opening else 0.0


def get_dashboard_stats(today: date) -> dict:
    month_start = today.replace(day=1)
    if month_start.month == 1:
        last_month_start = date(month_start.year - 1, 12, 1)
        last_month_end = date(month_start.year - 1, 12, 31)
    else:
        last_month_start = date(month_start.year, month_start.month - 1, 1)
        last_month_end = month_start - timedelta(days=1)

    last_month_purchases = ShPurchase.query.filter(
        ShPurchase.date_purchased >= last_month_start,
        ShPurchase.date_purchased <= last_month_end,
    ).all()

    last_month_total_amount = sum(p.total_amount for p in last_month_purchases)
    last_month_total_kg = sum(p.total_kg for p in last_month_purchases)

    latest = (
        ShPurchase.query.order_by(
            ShPurchase.date_purchased.desc(), ShPurchase.id.desc()
        ).first()
    )

    total_outstanding = (
        db.session.query(
            func.coalesce(func.sum(ShPurchase.total_amount - ShPurchase.paid_amount), 0)
        ).scalar()
        or 0
    )

    recent_purchases = (
        ShPurchase.query.order_by(
            ShPurchase.date_purchased.desc(), ShPurchase.id.desc()
        )
        .limit(10)
        .all()
    )

    return {
        "last_month_label": last_month_start.strftime("%B %Y"),
        "last_month_total_amount": float(last_month_total_amount),
        "last_month_total_kg": float(last_month_total_kg),
        "last_month_count": len(last_month_purchases),
        "latest_purchase": latest,
        "total_outstanding": float(total_outstanding),
        "ledger_balance": get_current_ledger_balance(),
        "opening_balance": float(get_opening_balance().amount)
        if get_opening_balance()
        else None,
        "recent_purchases": recent_purchases,
    }


def get_purchase_pdf_rows(supplier_id: Optional[int] = None) -> list[dict]:
    query = ShPurchase.query.join(ShSupplierCompany).join(ShClientCompany)
    if supplier_id:
        query = query.filter(ShPurchase.supplier_company_id == supplier_id)
    purchases = query.order_by(ShPurchase.date_purchased.desc()).all()
    return [_normalize_purchase_row(p) for p in purchases]


def get_ledger_pdf_rows() -> list[dict]:
    opening = get_opening_balance()
    balance = float(opening.amount) if opening else 0.0
    rows = []
    if opening:
        rows.append(
            {
                "date": opening.created_at.strftime("%d-%m-%Y"),
                "debit": "—",
                "credit": f"{opening.amount:,.2f}",
                "notes": opening.notes or "Opening balance",
                "balance": f"{balance:,.2f}",
            }
        )
    for item in get_ledger_rows():
        entry = item["entry"]
        balance = item["balance"]
        rows.append(
            {
                "date": entry.entry_date.strftime("%d-%m-%Y"),
                "debit": f"{entry.debit:,.2f}" if entry.debit else "—",
                "credit": f"{entry.credit:,.2f}" if entry.credit else "—",
                "notes": entry.notes or "—",
                "balance": f"{balance:,.2f}",
            }
        )
    return rows


def _normalize_purchase_row(purchase: ShPurchase) -> dict:
    return {
        "date": purchase.date_purchased.strftime("%d-%m-%Y"),
        "supplier": purchase.supplier.name,
        "material": purchase.material_name,
        "size": purchase.size or "—",
        "micron": purchase.micron or "—",
        "total_kg": f"{purchase.total_kg:,.1f}",
        "rate_1000": f"{purchase.rate_per_1000_kg:,.2f}",
        "total_amount": f"{purchase.total_amount:,.2f}",
        "paid": f"{purchase.paid_amount:,.2f}",
        "amount_due": f"{purchase.amount_due:,.2f}",
        "client": purchase.client.name,
        "notes": purchase.notes or "—",
    }
