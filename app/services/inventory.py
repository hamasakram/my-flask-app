from datetime import date
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import AppSetting, AuditLog, InkType, InventoryTransaction, OpeningStock


def get_or_create_ink_type(
    company_id: int,
    ink_name: str,
    color_code: str = "",
    unit_type: str = "",
) -> InkType:
    """Legacy helper — prefer create_ink_type + catalog selection in forms."""
    cleaned = ink_name.strip()
    if not cleaned:
        raise ValueError("Ink name is required.")

    ink = InkType.query.filter_by(company_id=company_id, name=cleaned).first()
    if ink:
        if color_code:
            ink.color_code = color_code.strip()
        if unit_type:
            ink.unit_type = unit_type.strip()
        return ink

    return create_ink_type(company_id, cleaned, color_code=color_code, unit_type=unit_type)


def create_ink_type(
    company_id: int,
    ink_name: str,
    color_code: str = "",
    unit_type: str = "",
) -> InkType:
    cleaned = ink_name.strip()
    if not cleaned:
        raise ValueError("Ink name is required.")

    existing = InkType.query.filter_by(company_id=company_id, name=cleaned).first()
    if existing:
        raise ValueError(f"Ink '{cleaned}' already exists for this company.")

    ink = InkType(
        company_id=company_id,
        name=cleaned,
        color_code=color_code.strip() or None,
        unit_type=unit_type.strip() or None,
    )
    db.session.add(ink)
    db.session.flush()
    return ink


def get_low_stock_threshold(ink: InkType) -> int:
    if ink.low_stock_threshold is not None:
        return ink.low_stock_threshold
    setting = AppSetting.query.filter_by(key="default_low_stock_threshold").first()
    if setting:
        return int(setting.value)
    return 50


def get_opening_quantity(company_id: int, ink_type_id: int) -> float:
    opening = OpeningStock.query.filter_by(
        company_id=company_id, ink_type_id=ink_type_id
    ).first()
    return opening.quantity if opening else 0.0


def _sum_transactions(
    company_id: int,
    ink_type_id: int,
    transaction_type: str,
) -> float:
    total = (
        db.session.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0))
        .filter_by(
            company_id=company_id,
            ink_type_id=ink_type_id,
            transaction_type=transaction_type,
        )
        .scalar()
    )
    return float(total)


def get_stored_stock(company_id: int, ink_type_id: int) -> float:
    opening = get_opening_quantity(company_id, ink_type_id)
    received = _sum_transactions(
        company_id, ink_type_id, InventoryTransaction.TRANSACTION_RECEIVED
    )
    issued = _sum_transactions(
        company_id, ink_type_id, InventoryTransaction.TRANSACTION_ISSUED
    )
    return opening + received - issued


def get_active_stock(company_id: int, ink_type_id: int) -> float:
    issued = _sum_transactions(
        company_id, ink_type_id, InventoryTransaction.TRANSACTION_ISSUED
    )
    used = _sum_transactions(
        company_id, ink_type_id, InventoryTransaction.TRANSACTION_USED
    )
    return issued - used


def get_transaction_totals(
    company_id: Optional[int] = None,
    ink_type_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    query = db.session.query(
        InventoryTransaction.company_id,
        InventoryTransaction.ink_type_id,
        InventoryTransaction.transaction_type,
        func.coalesce(func.sum(InventoryTransaction.quantity), 0).label("total"),
    )

    if company_id:
        query = query.filter(InventoryTransaction.company_id == company_id)
    if ink_type_id:
        query = query.filter(InventoryTransaction.ink_type_id == ink_type_id)
    if start_date:
        query = query.filter(InventoryTransaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(InventoryTransaction.transaction_date <= end_date)

    return query.group_by(
        InventoryTransaction.company_id,
        InventoryTransaction.ink_type_id,
        InventoryTransaction.transaction_type,
    ).all()


def calculate_live_stock(
    company_id: Optional[int] = None,
    ink_type_id: Optional[int] = None,
) -> list[dict]:
    ink_query = InkType.query
    if company_id:
        ink_query = ink_query.filter_by(company_id=company_id)
    if ink_type_id:
        ink_query = ink_query.filter_by(id=ink_type_id)

    inks = ink_query.order_by(InkType.company_id, InkType.name).all()
    results = []

    for ink in inks:
        opening = get_opening_quantity(ink.company_id, ink.id)
        received = _sum_transactions(
            ink.company_id, ink.id, InventoryTransaction.TRANSACTION_RECEIVED
        )
        issued = _sum_transactions(
            ink.company_id, ink.id, InventoryTransaction.TRANSACTION_ISSUED
        )
        used = _sum_transactions(
            ink.company_id, ink.id, InventoryTransaction.TRANSACTION_USED
        )
        stored = opening + received - issued
        active = max(0.0, issued - used)
        threshold = get_low_stock_threshold(ink)

        results.append(
            {
                "company": ink.company,
                "ink_type": ink,
                "opening": opening,
                "received": received,
                "issued": issued,
                "used": used,
                "stored": stored,
                "active": active,
                "current": active,
                "threshold": threshold,
                "is_low": active <= threshold,
            }
        )

    return results


def get_current_stock(company_id: int, ink_type_id: int) -> float:
    """Active (in-use) stock — used by daily usage forms."""
    return get_active_stock(company_id, ink_type_id)


def calculate_used_from_left(company_id: int, ink_type_id: int, quantity_left: float) -> float:
    current_stock = get_active_stock(company_id, ink_type_id)
    if quantity_left > current_stock:
        raise ValueError(
            f"Quantity left ({quantity_left}) cannot exceed in-use stock ({current_stock:.1f})."
        )
    return current_stock - quantity_left


def get_stock_usage_records(limit: int = 30) -> list[InventoryTransaction]:
    return (
        InventoryTransaction.query.filter_by(
            transaction_type=InventoryTransaction.TRANSACTION_USED
        )
        .order_by(
            InventoryTransaction.transaction_date.desc(),
            InventoryTransaction.id.desc(),
        )
        .limit(limit)
        .all()
    )


def get_recent_received_records(limit: int = 30) -> list[InventoryTransaction]:
    return (
        InventoryTransaction.query.filter_by(
            transaction_type=InventoryTransaction.TRANSACTION_RECEIVED
        )
        .order_by(
            InventoryTransaction.transaction_date.desc(),
            InventoryTransaction.id.desc(),
        )
        .limit(limit)
        .all()
    )


def get_recent_issued_records(limit: int = 30) -> list[InventoryTransaction]:
    return (
        InventoryTransaction.query.filter_by(
            transaction_type=InventoryTransaction.TRANSACTION_ISSUED
        )
        .order_by(
            InventoryTransaction.transaction_date.desc(),
            InventoryTransaction.id.desc(),
        )
        .limit(limit)
        .all()
    )


def get_dashboard_stats(today: date) -> dict:
    live_stock = calculate_live_stock()
    total_inventory = sum(item["active"] for item in live_stock)
    total_stored = sum(item["stored"] for item in live_stock)

    received_today = (
        db.session.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0))
        .filter(
            InventoryTransaction.transaction_type
            == InventoryTransaction.TRANSACTION_RECEIVED,
            InventoryTransaction.transaction_date == today,
        )
        .scalar()
    )
    issued_today = (
        db.session.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0))
        .filter(
            InventoryTransaction.transaction_type
            == InventoryTransaction.TRANSACTION_ISSUED,
            InventoryTransaction.transaction_date == today,
        )
        .scalar()
    )
    used_today = (
        db.session.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0))
        .filter(
            InventoryTransaction.transaction_type
            == InventoryTransaction.TRANSACTION_USED,
            InventoryTransaction.transaction_date == today,
        )
        .scalar()
    )

    by_company: dict[str, float] = {}
    by_ink: dict[str, float] = {}
    low_stock = []

    for item in live_stock:
        company_name = item["company"].name
        ink_name = item["ink_type"].name
        by_company[company_name] = by_company.get(company_name, 0) + item["active"]
        by_ink[ink_name] = by_ink.get(ink_name, 0) + item["active"]
        if item["is_low"]:
            low_stock.append(item)

    recent = (
        InventoryTransaction.query.order_by(
            InventoryTransaction.transaction_date.desc(),
            InventoryTransaction.id.desc(),
        )
        .limit(10)
        .all()
    )

    return {
        "total_inventory": total_inventory,
        "total_stored": total_stored,
        "received_today": float(received_today),
        "issued_today": float(issued_today),
        "used_today": float(used_today),
        "by_company": sorted(by_company.items(), key=lambda x: x[0]),
        "by_ink": sorted(by_ink.items(), key=lambda x: x[1], reverse=True)[:15],
        "low_stock": low_stock,
        "recent_transactions": recent,
    }


def log_audit(user_id, action, entity_type, entity_id=None, details=None):
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
    db.session.add(entry)
