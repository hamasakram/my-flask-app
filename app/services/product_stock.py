import sqlite3
from datetime import date
from typing import Optional, Type

from sqlalchemy import Float, case, func, literal_column
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.util import identity_key

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


def _threshold_lookup(setting_key: str):
    """get_threshold() for many items: the default setting is read at most once per call."""
    default = []

    def lookup(item) -> int:
        if getattr(item, "low_stock_threshold", None) is not None:
            return item.low_stock_threshold
        if not default:
            setting = AppSetting.query.filter_by(key=setting_key).first()
            default.append(int(setting.value) if setting else 50)
        return default[0]

    return lookup


def _scan_ordered_sum(txn_model):
    """SUM(quantity) that adds a group's rows in the order a plain per-item
    `SELECT sum(quantity) ... WHERE company_id = ? AND item_id = ? AND transaction_type = ?`
    reads them — Postgres: physical (ctid) order, which seq, bitmap and index scans all give
    for one key; SQLite: rowid order. Float addition depends on order, so without this a
    sort-based GROUP BY could change the last digit of a total. None if the database can't
    order inside an aggregate (callers then use the per-item queries)."""
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        order_key = "ctid"
    elif dialect == "sqlite" and sqlite3.sqlite_version_info >= (3, 44, 0):
        order_key = "rowid"
    else:
        return None
    table = txn_model.__table__.name
    return literal_column(f"sum({table}.quantity ORDER BY {table}.{order_key})", type_=Float)


def _per_item_totals(txn_model, item_fk: str, items) -> dict:
    """The original one-query-per-item totals (fallback for other databases)."""
    totals = {}
    for item in items:
        for txn_type in (txn_model.TRANSACTION_RECEIVED, txn_model.TRANSACTION_USED):
            totals[(item.company_id, item.id, txn_type)] = (
                db.session.query(func.coalesce(func.sum(txn_model.quantity), 0))
                .filter_by(
                    company_id=item.company_id,
                    **{item_fk: item.id},
                    transaction_type=txn_type,
                )
                .scalar()
            )
    return totals


def _load_missing_companies(items) -> list:
    """Load, in one query, the companies of `items` that the session doesn't hold yet (pages
    with a company dropdown already loaded most of them), so item.company never queries.
    The returned list must be kept alive while item.company is read (weak identity map)."""
    from app.models import Company

    identity_map = db.session.identity_map
    missing = {
        item.company_id
        for item in items
        if item.company_id is not None
        and identity_key(Company, item.company_id) not in identity_map
    }
    if not missing:
        return []
    return Company.query.filter(Company.id.in_(sorted(missing))).all()


def _stock_rows(
    item_model: Type,
    opening_model: Type,
    txn_model: Type,
    item_fk: str,
    threshold_key: str,
    company_id: Optional[int],
    item_id: Optional[int],
    with_company: bool,
):
    query = item_model.query
    if company_id:
        query = query.filter_by(company_id=company_id)
    if item_id:
        query = query.filter_by(id=item_id)

    items = query.order_by(item_model.company_id, item_model.name).all()
    results = []
    if not items:
        return results
    companies = _load_missing_companies(items) if with_company else []  # noqa: F841 (kept alive)
    item_id_col = getattr(txn_model, item_fk)
    opening_item_col = getattr(opening_model, item_fk)

    # Opening stock for every listed item in one query (unique per company + item; if a
    # duplicate ever existed the first row in scan order wins, as .first() did).
    opening_query = db.session.query(
        opening_model.company_id, opening_item_col, opening_model.quantity
    )
    if company_id:
        opening_query = opening_query.filter(opening_model.company_id == company_id)
    if item_id:
        opening_query = opening_query.filter(opening_item_col == item_id)
    openings = {}
    for row_company_id, row_item_id, quantity in opening_query:
        openings.setdefault((row_company_id, row_item_id), quantity)

    ordered_sum = _scan_ordered_sum(txn_model)
    if ordered_sum is None:
        totals = _per_item_totals(txn_model, item_fk, items)
    else:
        # Received / used totals per (company, item, type) in one GROUP BY query. The database
        # still does the summing, and adds each group's rows in the order the old per-item
        # query scanned them, so the float totals are bit-for-bit the same.
        totals_query = db.session.query(
            txn_model.company_id,
            item_id_col,
            txn_model.transaction_type,
            func.coalesce(ordered_sum, 0),
        )
        if company_id:
            totals_query = totals_query.filter(txn_model.company_id == company_id)
        if item_id:
            totals_query = totals_query.filter(item_id_col == item_id)
        totals = {
            (row_company_id, row_item_id, txn_type): total
            for row_company_id, row_item_id, txn_type, total in totals_query.group_by(
                txn_model.company_id, item_id_col, txn_model.transaction_type
            )
        }
    threshold_for = _threshold_lookup(threshold_key)

    for item in items:
        key = (item.company_id, item.id)
        opening_qty = openings[key] if key in openings else 0.0

        received = totals.get(key + (txn_model.TRANSACTION_RECEIVED,), 0)
        used = totals.get(key + (txn_model.TRANSACTION_USED,), 0)
        current = opening_qty + float(received) - float(used)
        threshold = threshold_for(item)

        results.append(
            {
                "company": item.company if with_company else None,
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


def calculate_live_stock_generic(
    item_model: Type,
    opening_model: Type,
    txn_model: Type,
    item_fk: str,
    threshold_key: str,
    company_id: Optional[int] = None,
    item_id: Optional[int] = None,
):
    return _stock_rows(
        item_model,
        opening_model,
        txn_model,
        item_fk,
        threshold_key,
        company_id,
        item_id,
        with_company=True,
    )


def get_current_stock_generic(
    item_model,
    opening_model,
    txn_model,
    item_fk,
    threshold_key,
    company_id: int,
    item_id: int,
) -> float:
    # Only the number is needed, so the item's company isn't loaded.
    rows = _stock_rows(
        item_model,
        opening_model,
        txn_model,
        item_fk,
        threshold_key,
        company_id,
        item_id,
        with_company=False,
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

    # Both of today's totals from one scan: each SUM only sees its own type's rows (the CASE
    # gives NULL, which SUM skips, for the other type), in the same order as before.
    received_today, used_today = (
        db.session.query(
            func.coalesce(
                func.sum(
                    case(
                        (
                            txn_model.transaction_type == txn_model.TRANSACTION_RECEIVED,
                            txn_model.quantity,
                        )
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            txn_model.transaction_type == txn_model.TRANSACTION_USED,
                            txn_model.quantity,
                        )
                    )
                ),
                0,
            ),
        )
        .filter(txn_model.transaction_date == today)
        .one()
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
        txn_model.query.options(joinedload(txn_model.company), joinedload(txn_model.item))
        .order_by(txn_model.transaction_date.desc(), txn_model.id.desc())
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
