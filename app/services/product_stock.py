from datetime import date
from typing import Optional, Type

from sqlalchemy import func

from app import db
from app.models import AppSetting


def get_or_create_item(
    item_model: Type,
    company_id: int,
    name: str,
    unit_type: str = "Kg",
    **extra_fields,
):
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Item name is required.")

    filters = {"company_id": company_id, "name": cleaned}
    item = item_model.query.filter_by(**filters).first()
    if item:
        if unit_type and hasattr(item, "unit_type"):
            item.unit_type = unit_type
        for key, value in extra_fields.items():
            if hasattr(item, key) and value is not None:
                setattr(item, key, value)
        return item

    payload = {"company_id": company_id, "name": cleaned, **extra_fields}
    if hasattr(item_model, "unit_type"):
        payload["unit_type"] = unit_type
    item = item_model(**payload)
    db.session.add(item)
    db.session.flush()
    return item


def get_threshold(item, setting_key: str) -> int:
    if getattr(item, "low_stock_threshold", None) is not None:
        return item.low_stock_threshold
    setting = AppSetting.query.filter_by(key=setting_key).first()
    if setting:
        return int(setting.value)
    return 50


def calculate_live_stock_generic(
    item_model: Type,
    opening_model: Type,
    txn_model: Type,
    item_fk: str,
    threshold_key: str,
    company_id: Optional[int] = None,
    item_id: Optional[int] = None,
):
    query = item_model.query
    if company_id:
        query = query.filter_by(company_id=company_id)
    if item_id:
        query = query.filter_by(id=item_id)

    items = query.order_by(item_model.company_id, item_model.name).all()
    results = []
    item_id_col = getattr(txn_model, item_fk)

    for item in items:
        opening = opening_model.query.filter_by(
            company_id=item.company_id, **{item_fk: item.id}
        ).first()
        opening_qty = opening.quantity if opening else 0.0

        received = (
            db.session.query(func.coalesce(func.sum(txn_model.quantity), 0))
            .filter_by(
                company_id=item.company_id,
                **{item_fk: item.id},
                transaction_type=txn_model.TRANSACTION_RECEIVED,
            )
            .scalar()
        )
        used = (
            db.session.query(func.coalesce(func.sum(txn_model.quantity), 0))
            .filter_by(
                company_id=item.company_id,
                **{item_fk: item.id},
                transaction_type=txn_model.TRANSACTION_USED,
            )
            .scalar()
        )
        current = opening_qty + float(received) - float(used)
        threshold = get_threshold(item, threshold_key)

        results.append(
            {
                "company": item.company,
                "item": item,
                "opening": opening_qty,
                "received": float(received),
                "used": float(used),
                "current": current,
                "threshold": threshold,
                "is_low": current <= threshold,
            }
        )

    return results


def get_current_stock_generic(
    item_model,
    opening_model,
    txn_model,
    item_fk,
    threshold_key,
    company_id: int,
    item_id: int,
) -> float:
    rows = calculate_live_stock_generic(
        item_model,
        opening_model,
        txn_model,
        item_fk,
        threshold_key,
        company_id=company_id,
        item_id=item_id,
    )
    return rows[0]["current"] if rows else 0.0


def calculate_used_from_left_generic(
    item_model,
    opening_model,
    txn_model,
    item_fk,
    threshold_key,
    company_id: int,
    item_id: int,
    quantity_left: float,
) -> float:
    current_stock = get_current_stock_generic(
        item_model,
        opening_model,
        txn_model,
        item_fk,
        threshold_key,
        company_id,
        item_id,
    )
    if quantity_left > current_stock:
        raise ValueError(
            f"Quantity left ({quantity_left}) cannot exceed current stock ({current_stock:.1f})."
        )
    return current_stock - quantity_left


def get_dashboard_stats_generic(
    item_model,
    opening_model,
    txn_model,
    item_fk,
    threshold_key,
    today: date,
):
    live_stock = calculate_live_stock_generic(
        item_model, opening_model, txn_model, item_fk, threshold_key
    )
    total_inventory = sum(item["current"] for item in live_stock)

    received_today = (
        db.session.query(func.coalesce(func.sum(txn_model.quantity), 0))
        .filter(
            txn_model.transaction_type == txn_model.TRANSACTION_RECEIVED,
            txn_model.transaction_date == today,
        )
        .scalar()
    )
    used_today = (
        db.session.query(func.coalesce(func.sum(txn_model.quantity), 0))
        .filter(
            txn_model.transaction_type == txn_model.TRANSACTION_USED,
            txn_model.transaction_date == today,
        )
        .scalar()
    )

    by_company = {}
    by_item = {}
    low_stock = []
    for row in live_stock:
        by_company[row["company"].name] = by_company.get(row["company"].name, 0) + row["current"]
        by_item[row["item"].display_name] = by_item.get(row["item"].display_name, 0) + row["current"]
        if row["is_low"]:
            low_stock.append(row)

    recent = (
        txn_model.query.order_by(txn_model.transaction_date.desc(), txn_model.id.desc())
        .limit(10)
        .all()
    )

    return {
        "total_inventory": total_inventory,
        "received_today": float(received_today),
        "used_today": float(used_today),
        "by_company": sorted(by_company.items()),
        "by_item": sorted(by_item.items(), key=lambda x: x[1], reverse=True)[:15],
        "low_stock": low_stock,
        "recent_transactions": recent,
    }
