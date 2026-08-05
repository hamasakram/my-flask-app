from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func, or_

from app import db
from app.models import (
    ShClientCompany,
    ShLedgerEntry,
    ShOpeningBalance,
    ShPaymentScreenshot,
    ShPurchase,
    ShSaleInvoice,
    ShSupplierCompany,
)


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
    """Remove duplicate supplier debits auto-created from purchase paid amounts."""
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


def _ledger_sum_by_purchase(purchase_ids: list[int], amount_attr: str) -> dict[int, float]:
    """Sum ledger debits/credits already linked to specific purchases."""
    if not purchase_ids:
        return {}
    amount_col = getattr(ShLedgerEntry, amount_attr)
    rows = (
        db.session.query(
            ShLedgerEntry.purchase_id,
            func.coalesce(func.sum(amount_col), 0),
        )
        .filter(ShLedgerEntry.purchase_id.in_(purchase_ids))
        .group_by(ShLedgerEntry.purchase_id)
        .all()
    )
    return {purchase_id: float(amount) for purchase_id, amount in rows}


def _party_level_ledger_pool(
    party_ids: list[int], party_attr: str, amount_attr: str
) -> dict[int, float]:
    """Sum party-level ledger amounts (no purchase link) per supplier/client."""
    if not party_ids:
        return {}
    party_col = getattr(ShLedgerEntry, party_attr)
    amount_col = getattr(ShLedgerEntry, amount_attr)
    rows = (
        db.session.query(
            party_col,
            func.coalesce(func.sum(amount_col), 0),
        )
        .filter(
            ShLedgerEntry.purchase_id.is_(None),
            party_col.in_(party_ids),
            amount_col > 0,
        )
        .group_by(party_col)
        .all()
    )
    return {party_id: float(amount) for party_id, amount in rows}


def _fifo_allocate_party_ledger(
    purchases: list[ShPurchase],
    party_id_fn: Callable[[ShPurchase], Optional[int]],
    direct_by_purchase: dict[int, float],
    party_pool: dict[int, float],
    due_before_party_ledger_fn: Callable[[ShPurchase], float],
) -> dict[int, float]:
    """Apply party-level ledger to purchases oldest-first (FIFO)."""
    allocated = {purchase.id: direct_by_purchase.get(purchase.id, 0.0) for purchase in purchases}
    by_party: dict[int, list[ShPurchase]] = defaultdict(list)
    for purchase in purchases:
        party_id = party_id_fn(purchase)
        if party_id is not None:
            by_party[party_id].append(purchase)

    for party_id, party_purchases in by_party.items():
        remaining_pool = party_pool.get(party_id, 0.0)
        if remaining_pool <= 0:
            continue
        for purchase in sorted(
            party_purchases,
            key=lambda p: (p.date_purchased, p.id),
        ):
            if remaining_pool <= 0:
                break
            already_applied = allocated.get(purchase.id, 0.0)
            due = due_before_party_ledger_fn(purchase) - already_applied
            if due <= 0:
                continue
            applied = min(due, remaining_pool)
            allocated[purchase.id] = already_applied + applied
            remaining_pool -= applied

    return allocated


def get_supplier_party_balances() -> list[dict]:
    """Amount to pay each supplier — auto from purchases minus ledger payments."""
    return get_supplier_purchase_balance_rows()


def get_supplier_purchase_balance_rows() -> list[dict]:
    """One row per advance stock purchase — supplier amount still due."""
    purchases = (
        ShPurchase.query.join(ShSupplierCompany)
        .order_by(ShPurchase.date_purchased.desc(), ShPurchase.id.desc())
        .all()
    )
    purchase_ids = [purchase.id for purchase in purchases]
    supplier_ids = list({purchase.supplier_company_id for purchase in purchases})

    direct_ledger = _ledger_sum_by_purchase(purchase_ids, "debit")
    party_ledger_pool = _party_level_ledger_pool(
        supplier_ids, "supplier_company_id", "debit"
    )

    screenshot_by_purchase: dict[int, float] = {}
    if purchase_ids:
        screenshot_rows = (
            db.session.query(
                ShPaymentScreenshot.purchase_id,
                func.coalesce(func.sum(ShPaymentScreenshot.amount_paid), 0),
            )
            .filter(ShPaymentScreenshot.purchase_id.in_(purchase_ids))
            .group_by(ShPaymentScreenshot.purchase_id)
            .all()
        )
        screenshot_by_purchase = {
            purchase_id: float(amount) for purchase_id, amount in screenshot_rows
        }

    def due_before_party_ledger(purchase: ShPurchase) -> float:
        total_purchased = float(purchase.total_amount or 0)
        paid_on_purchase = float(purchase.paid_amount or 0)
        screenshot_payments = screenshot_by_purchase.get(purchase.id, 0.0)
        return max(
            0.0,
            total_purchased - paid_on_purchase - screenshot_payments,
        )

    ledger_allocated = _fifo_allocate_party_ledger(
        purchases,
        lambda purchase: purchase.supplier_company_id,
        direct_ledger,
        party_ledger_pool,
        due_before_party_ledger,
    )

    rows = []
    for purchase in purchases:
        paid_on_purchase = float(purchase.paid_amount or 0)
        total_purchased = float(purchase.total_amount or 0)
        ledger_payments = ledger_allocated.get(purchase.id, 0.0)
        screenshot_payments = screenshot_by_purchase.get(purchase.id, 0.0)
        total_paid = paid_on_purchase + ledger_payments + screenshot_payments
        balance_to_pay = max(0.0, total_purchased - total_paid)

        rows.append(
            {
                "purchase": purchase,
                "party": purchase.supplier,
                "client": purchase.client,
                "total_purchased": total_purchased,
                "paid_on_purchases": paid_on_purchase,
                "ledger_payments": ledger_payments,
                "screenshot_payments": screenshot_payments,
                "total_paid": total_paid,
                "purchase_due_on_records": float(purchase.amount_due),
                "balance_to_pay": balance_to_pay,
            }
        )
    return rows


def get_client_party_balances() -> list[dict]:
    """Amount to receive from each client — one row per advance stock purchase."""
    return get_client_purchase_balance_rows()


def get_client_purchase_balance_rows() -> list[dict]:
    """One row per advance stock purchase — client amount still to receive."""
    purchases = (
        ShPurchase.query.join(ShClientCompany)
        .filter(ShPurchase.client_total_amount.isnot(None))
        .order_by(ShPurchase.date_purchased.desc(), ShPurchase.id.desc())
        .all()
    )
    purchase_ids = [purchase.id for purchase in purchases]
    client_ids = list({purchase.client_company_id for purchase in purchases})

    direct_ledger = _ledger_sum_by_purchase(purchase_ids, "credit")
    party_ledger_pool = _party_level_ledger_pool(
        client_ids, "client_company_id", "credit"
    )
    ledger_allocated = _fifo_allocate_party_ledger(
        purchases,
        lambda purchase: purchase.client_company_id,
        direct_ledger,
        party_ledger_pool,
        lambda purchase: float(purchase.client_total_amount or 0),
    )

    rows = []
    for purchase in purchases:
        client_total = float(purchase.client_total_amount or 0)
        ledger_received = ledger_allocated.get(purchase.id, 0.0)
        balance_to_receive = max(0.0, client_total - ledger_received)

        rows.append(
            {
                "purchase": purchase,
                "party": purchase.client,
                "supplier": purchase.supplier,
                "purchase_billed": client_total,
                "ledger_received": ledger_received,
                "total_billed": client_total,
                "balance_to_receive": balance_to_receive,
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
