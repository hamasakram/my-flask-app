from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import (
    ShClientCompany,
    ShLedgerEntry,
    ShOpeningBalance,
    ShPaymentScreenshot,
    ShPurchase,
    ShSupplierCompany,
)


def calculate_total_amount(total_kg: float, rate_per_kg: float) -> float:
    """Total = Total Purchased (KG) × Amount / KG."""
    if not total_kg or not rate_per_kg:
        return 0.0
    return float(total_kg) * float(rate_per_kg)


def calculate_gate_pass_total(net_weight: float, amount_per_kg: float) -> float:
    """Total Amount = Net Weight (KG) × Amount Per KG."""
    if not net_weight or not amount_per_kg:
        return 0.0
    return float(net_weight) * float(amount_per_kg)


def parse_issued_datetime(date_str: str, time_str: str) -> datetime:
    """Accept HH:MM or HH:MM:SS from browser time inputs."""
    cleaned_time = (time_str or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {cleaned_time}", fmt)
        except ValueError:
            continue
    raise ValueError("Invalid date or time.")


def parse_roll_gross_weights(form) -> list[float]:
    weights = []
    for raw in form.getlist("roll_gross_weight"):
        if raw in (None, ""):
            continue
        value = float(raw)
        if value <= 0:
            raise ValueError("Each roll must have a gross weight greater than zero.")
        weights.append(value)
    return weights


def compute_gate_pass_weights(
    gross_weights: list[float],
    cone_weight_per_roll: float = 0.0,
) -> dict:
    if not gross_weights:
        raise ValueError("Add at least one roll with its gross weight.")

    cone_per_roll = float(cone_weight_per_roll or 0)
    roll_count = len(gross_weights)
    gross_total = sum(gross_weights)
    cone_total = cone_per_roll * roll_count
    net_total = gross_total - cone_total
    if net_total <= 0:
        raise ValueError("Total net weight must be greater than zero.")

    return {
        "rolls": roll_count,
        "gross_weight": gross_total,
        "cone_total": cone_total,
        "net_weight": net_total,
        "gross_weight_per_roll": gross_total / roll_count if roll_count else None,
        "net_weight_per_roll": net_total / roll_count if roll_count else None,
    }


def save_gate_pass_rolls(gate_pass, gross_weights: list[float]) -> None:
    from app.models import ShGatePassRoll

    gate_pass.roll_items = [
        ShGatePassRoll(roll_number=index, gross_weight=weight)
        for index, weight in enumerate(gross_weights, start=1)
    ]


def next_gate_pass_number() -> str:
    from app.models import ShGatePass

    year = datetime.now().year
    prefix = f"GP-{year}-"
    latest = (
        ShGatePass.query.filter(ShGatePass.gate_pass_number.like(f"{prefix}%"))
        .order_by(ShGatePass.id.desc())
        .first()
    )
    if latest:
        try:
            seq = int(latest.gate_pass_number.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = latest.id + 1
    else:
        seq = 1
    return f"{prefix}{seq:05d}"


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


def get_supplier_party_balances() -> list[dict]:
    """Amount to pay each supplier — auto from purchases minus ledger payments."""
    suppliers = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    rows = []
    for supplier in suppliers:
        purchases = ShPurchase.query.filter_by(supplier_company_id=supplier.id).all()
        total_purchased = sum(float(p.total_amount or 0) for p in purchases)
        paid_on_purchases = sum(float(p.paid_amount or 0) for p in purchases)
        purchase_due = sum(float(p.amount_due) for p in purchases)

        ledger_payments = (
            db.session.query(func.coalesce(func.sum(ShLedgerEntry.debit), 0))
            .filter(ShLedgerEntry.supplier_company_id == supplier.id)
            .scalar()
            or 0
        )
        screenshot_payments = (
            db.session.query(func.coalesce(func.sum(ShPaymentScreenshot.amount_paid), 0))
            .filter(ShPaymentScreenshot.supplier_company_id == supplier.id)
            .scalar()
            or 0
        )

        balance_to_pay = max(0.0, purchase_due - float(ledger_payments))

        rows.append(
            {
                "party": supplier,
                "total_purchased": float(total_purchased),
                "paid_on_purchases": float(paid_on_purchases),
                "ledger_payments": float(ledger_payments),
                "screenshot_payments": float(screenshot_payments),
                "balance_to_pay": float(balance_to_pay),
                "purchase_count": len(purchases),
            }
        )
    return rows


def get_client_party_balances() -> list[dict]:
    """Amount to receive from each client — auto from purchases minus ledger receipts."""
    clients = ShClientCompany.query.order_by(ShClientCompany.name).all()
    rows = []
    for client in clients:
        purchases = ShPurchase.query.filter_by(client_company_id=client.id).all()
        total_billed = sum(float(p.client_total_amount or 0) for p in purchases)

        ledger_received = (
            db.session.query(func.coalesce(func.sum(ShLedgerEntry.credit), 0))
            .filter(ShLedgerEntry.client_company_id == client.id)
            .scalar()
            or 0
        )

        balance_to_receive = max(0.0, float(total_billed) - float(ledger_received))

        rows.append(
            {
                "party": client,
                "total_billed": float(total_billed),
                "ledger_received": float(ledger_received),
                "balance_to_receive": float(balance_to_receive),
                "purchase_count": len(purchases),
            }
        )
    return rows


def get_party_balance_totals() -> dict:
    suppliers = get_supplier_party_balances()
    clients = get_client_party_balances()
    return {
        "total_payable": sum(r["balance_to_pay"] for r in suppliers),
        "total_receivable": sum(r["balance_to_receive"] for r in clients),
        "supplier_rows": suppliers,
        "client_rows": clients,
    }


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

    party_totals = get_party_balance_totals()

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
        "total_payable": party_totals["total_payable"],
        "total_receivable": party_totals["total_receivable"],
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
        "client_rate": f"{purchase.client_rate_per_kg:,.2f}"
        if purchase.client_rate_per_kg
        else "—",
        "client_total": f"{purchase.client_total_amount:,.2f}"
        if purchase.client_total_amount
        else "—",
        "notes": purchase.notes or "—",
    }
