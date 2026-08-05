from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import ShPaymentScreenshot, ShPurchase
from app.services.sh_bank import filter_by_bank, get_current_sh_bank


def calculate_total_amount(total_kg: float, rate_per_kg: float) -> float:
    """Total = Total Purchased (KG) × Amount / KG."""
    if not total_kg or not rate_per_kg:
        return 0.0
    return float(total_kg) * float(rate_per_kg)


def parse_multi_item_purchase_lines(form) -> list[dict]:
    """Parse multiple material lines for one purchase entry."""
    materials = form.getlist("item_material_name")
    sizes = form.getlist("item_size")
    microns = form.getlist("item_micron")
    rates = form.getlist("item_rate_per_kg")
    client_rates = form.getlist("item_client_rate_per_kg")
    total_kgs = form.getlist("item_total_kg")
    lines = []

    for index, raw_material in enumerate(materials):
        material_name = (raw_material or "").strip()
        if not material_name:
            continue

        size = sizes[index].strip() if index < len(sizes) else ""
        micron = microns[index].strip() if index < len(microns) else ""

        rate_raw = rates[index] if index < len(rates) else ""
        try:
            rate_per_kg = float(rate_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Item {index + 1}: enter a valid Amount / KG.") from exc
        if rate_per_kg <= 0:
            raise ValueError(f"Item {index + 1}: Amount / KG must be greater than zero.")

        client_rate_raw = client_rates[index] if index < len(client_rates) else "0"
        try:
            client_rate = float(client_rate_raw or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Item {index + 1}: enter a valid client rate.") from exc
        if client_rate < 0:
            raise ValueError(f"Item {index + 1}: client rate cannot be negative.")

        kg_raw = total_kgs[index] if index < len(total_kgs) else ""
        try:
            total_kg = float(kg_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Item {index + 1}: enter a valid KG amount.") from exc
        if total_kg <= 0:
            raise ValueError(f"Item {index + 1}: KG must be greater than zero.")

        lines.append(
            {
                "material_name": material_name,
                "size": size,
                "micron": micron,
                "rate_per_kg": rate_per_kg,
                "client_rate_per_kg": client_rate,
                "total_kg": total_kg,
            }
        )

    if not lines:
        raise ValueError("Add at least one item with material name and KG.")
    return lines


def remove_auto_synced_purchase_ledger_entries() -> None:
    """Legacy cleanup — remove duplicate supplier debits auto-created from purchases."""
    from app.models import ShLedgerEntry
    from sqlalchemy import or_

    ShLedgerEntry.query.filter(ShLedgerEntry.purchase_id.isnot(None)).delete(
        synchronize_session=False
    )
    ShLedgerEntry.query.filter(
        or_(
            ShLedgerEntry.notes.like("Payment on purchase entry%"),
            ShLedgerEntry.notes.like("Received from client on purchase%"),
        )
    ).delete(synchronize_session=False)
    db.session.commit()


def calculate_gate_pass_total(net_weight: float, amount_per_kg: float) -> float:
    if not net_weight or not amount_per_kg:
        return 0.0
    return float(net_weight) * float(amount_per_kg)


def parse_issued_datetime(date_str: str, time_str: str) -> datetime:
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

    for roll in list(gate_pass.roll_items):
        db.session.delete(roll)
    db.session.flush()

    for index, weight in enumerate(gross_weights, start=1):
        db.session.add(
            ShGatePassRoll(
                gate_pass_id=gate_pass.id,
                roll_number=index,
                gross_weight=weight,
            )
        )


def next_gate_pass_number() -> str:
    from app.models import ShGatePass

    year = datetime.now().year
    prefix = f"GP-{year}-"
    query = ShGatePass.query.filter(ShGatePass.gate_pass_number.like(f"{prefix}%"))
    query = filter_by_bank(query, ShGatePass)
    latest = query.order_by(ShGatePass.id.desc()).first()
    if latest:
        try:
            seq = int(latest.gate_pass_number.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = latest.id + 1
    else:
        seq = 1
    return f"{prefix}{seq:05d}"


def _scoped_purchase_query():
    return filter_by_bank(ShPurchase.query, ShPurchase)


def get_dashboard_stats(today: date) -> dict:
    month_start = today.replace(day=1)
    if month_start.month == 1:
        last_month_start = date(month_start.year - 1, 12, 1)
        last_month_end = date(month_start.year - 1, 12, 31)
    else:
        last_month_start = date(month_start.year, month_start.month - 1, 1)
        last_month_end = month_start - timedelta(days=1)

    purchase_q = _scoped_purchase_query()
    last_month_purchases = purchase_q.filter(
        ShPurchase.date_purchased >= last_month_start,
        ShPurchase.date_purchased <= last_month_end,
    ).all()

    last_month_total_amount = sum(p.total_amount for p in last_month_purchases)
    last_month_total_kg = sum(p.total_kg for p in last_month_purchases)

    latest = purchase_q.order_by(
        ShPurchase.date_purchased.desc(), ShPurchase.id.desc()
    ).first()

    total_outstanding = (
        purchase_q.with_entities(
            func.coalesce(func.sum(ShPurchase.total_amount - ShPurchase.paid_amount), 0)
        ).scalar()
        or 0
    )

    recent_purchases = (
        purchase_q.order_by(ShPurchase.date_purchased.desc(), ShPurchase.id.desc())
        .limit(10)
        .all()
    )

    bank = get_current_sh_bank()
    opening_balance = float(bank.opening_balance) if bank else None

    return {
        "last_month_label": last_month_start.strftime("%B %Y"),
        "last_month_total_amount": float(last_month_total_amount),
        "last_month_total_kg": float(last_month_total_kg),
        "last_month_count": len(last_month_purchases),
        "latest_purchase": latest,
        "total_outstanding": float(total_outstanding),
        "opening_balance": opening_balance,
        "current_bank": bank,
        "recent_purchases": recent_purchases,
    }


def get_purchase_pdf_rows(supplier_id: Optional[int] = None) -> list[dict]:
    query = _scoped_purchase_query().join(ShPurchase.supplier).join(ShPurchase.client)
    if supplier_id:
        query = query.filter(ShPurchase.supplier_company_id == supplier_id)
    purchases = query.order_by(ShPurchase.date_purchased.desc()).all()
    return [_normalize_purchase_row(p) for p in purchases]


def get_ledger_pdf_rows() -> list[dict]:
    """Legacy helper for custom PDF builder — uses client ledger entries."""
    from app.models import ShClientLedgerEntry
    from app.services.sh_manual_ledger import get_client_ledger_entries

    rows = []
    bank = get_current_sh_bank()
    if bank and bank.opening_balance:
        rows.append(
            {
                "date": bank.created_at.strftime("%d-%m-%Y") if bank.created_at else "—",
                "debit": "—",
                "credit": f"{bank.opening_balance:,.2f}",
                "notes": f"Opening balance ({bank.name})",
                "balance": f"{bank.opening_balance:,.2f}",
            }
        )

    balance = float(bank.opening_balance) if bank else 0.0
    for entry in reversed(get_client_ledger_entries()):
        balance += float(entry.total_amount or 0)
        rows.append(
            {
                "date": entry.entry_date.strftime("%d-%m-%Y"),
                "debit": "—",
                "credit": f"{entry.total_amount:,.2f}" if entry.total_amount else "—",
                "notes": entry.reference_number + (f" — {entry.notes}" if entry.notes else ""),
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
