from datetime import date
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import InkType, InventoryTransaction
from app.services.companies import get_or_create_default_ink_company


def create_ink(
    ink_company: str,
    color_code: str,
    color: str,
    total_cans: float,
    weight_per_can: float,
) -> InkType:
    cleaned_company = ink_company.strip()
    cleaned_code = color_code.strip()
    cleaned_color = color.strip()

    if not cleaned_company or not cleaned_color:
        raise ValueError("Ink company and color are required.")

    company = get_or_create_default_ink_company()
    ink = InkType(
        company_id=company.id,
        ink_company=cleaned_company,
        color_code=cleaned_code or None,
        name=cleaned_color,
        initial_cans=float(total_cans or 0),
        weight_per_can=float(weight_per_can or 0),
        unit_type="Can",
    )
    db.session.add(ink)
    db.session.flush()
    return ink


def list_inks() -> list[InkType]:
    return InkType.query.order_by(InkType.ink_company, InkType.name, InkType.color_code).all()


def get_ink_options() -> list[dict]:
    return [{"id": i.id, "name": i.display_name} for i in list_inks()]


def get_cans_in_total(ink_id: int) -> float:
    return float(
        db.session.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0))
        .filter_by(
            ink_type_id=ink_id,
            transaction_type=InventoryTransaction.TRANSACTION_CANS_IN,
        )
        .scalar()
    )


def get_cans_used_total(ink_id: int) -> float:
    return float(
        db.session.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0))
        .filter_by(
            ink_type_id=ink_id,
            transaction_type=InventoryTransaction.TRANSACTION_CANS_LEFT,
        )
        .scalar()
    )


def get_current_cans(ink: InkType) -> float:
    return float(ink.initial_cans) + get_cans_in_total(ink.id) - get_cans_used_total(ink.id)


def get_current_weight(ink: InkType) -> float:
    return get_current_cans(ink) * float(ink.weight_per_can)


def calculate_live_stock(ink_id: Optional[int] = None) -> list[dict]:
    query = InkType.query
    if ink_id:
        query = query.filter_by(id=ink_id)

    results = []
    for ink in query.order_by(InkType.ink_company, InkType.name, InkType.color_code).all():
        cans_in = get_cans_in_total(ink.id)
        used = get_cans_used_total(ink.id)
        current_cans = float(ink.initial_cans) + cans_in - used
        current_weight = current_cans * float(ink.weight_per_can)
        results.append(
            {
                "ink": ink,
                "initial_cans": float(ink.initial_cans),
                "initial_weight": float(ink.initial_total_weight),
                "cans_in": cans_in,
                "used": used,
                "current_cans": current_cans,
                "current_weight": current_weight,
            }
        )
    return results


def calculate_used_from_left(ink_id: int, cans_left: float) -> float:
    ink = db.session.get(InkType, ink_id)
    if not ink:
        raise ValueError("Ink not found.")

    current_cans = get_current_cans(ink)
    if cans_left > current_cans:
        raise ValueError(
            f"Cans left ({cans_left}) cannot exceed current cans ({current_cans:.1f})."
        )
    return current_cans - cans_left


def get_dashboard_stats(today: date) -> dict:
    live_stock = calculate_live_stock()
    total_cans = sum(item["current_cans"] for item in live_stock)
    total_weight = sum(item["current_weight"] for item in live_stock)

    cans_in_today = float(
        db.session.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0))
        .filter(
            InventoryTransaction.transaction_type == InventoryTransaction.TRANSACTION_CANS_IN,
            InventoryTransaction.transaction_date == today,
        )
        .scalar()
    )
    used_today = float(
        db.session.query(func.coalesce(func.sum(InventoryTransaction.quantity), 0))
        .filter(
            InventoryTransaction.transaction_type == InventoryTransaction.TRANSACTION_CANS_LEFT,
            InventoryTransaction.transaction_date == today,
        )
        .scalar()
    )

    recent = (
        InventoryTransaction.query.order_by(
            InventoryTransaction.transaction_date.desc(),
            InventoryTransaction.id.desc(),
        )
        .limit(10)
        .all()
    )

    return {
        "total_cans": total_cans,
        "total_weight": total_weight,
        "cans_in_today": cans_in_today,
        "used_today": used_today,
        "live_stock": live_stock,
        "recent_transactions": recent,
    }


def get_daily_report_data(report_date: date) -> dict:
    live_stock = calculate_live_stock()
    day_txns = (
        InventoryTransaction.query.filter_by(transaction_date=report_date)
        .order_by(InventoryTransaction.id)
        .all()
    )

    cans_in_today = [
        t for t in day_txns if t.transaction_type == InventoryTransaction.TRANSACTION_CANS_IN
    ]
    cans_left_today = [
        t for t in day_txns if t.transaction_type == InventoryTransaction.TRANSACTION_CANS_LEFT
    ]

    return {
        "report_date": report_date,
        "live_stock": live_stock,
        "cans_in_today": cans_in_today,
        "cans_left_today": cans_left_today,
        "total_cans_in": sum(t.quantity for t in cans_in_today),
        "total_used": sum(t.quantity for t in cans_left_today),
        "grouped_companies": group_report_by_company(live_stock, cans_in_today, cans_left_today),
    }


def group_report_by_company(live_stock, cans_in_today, cans_left_today) -> list[dict]:
    companies: dict[str, dict] = {}

    def ensure_company(name: str) -> dict:
        key = name or "Uncategorized"
        if key not in companies:
            companies[key] = {
                "name": key,
                "inks": {},
                "total_cans_left": 0.0,
                "total_weight_left": 0.0,
                "cans_in_today": 0.0,
                "used_today": 0.0,
            }
        return companies[key]

    for item in live_stock:
        comp = ensure_company(item["ink"].ink_company)
        ink_id = item["ink"].id
        if ink_id not in comp["inks"]:
            comp["inks"][ink_id] = {
                "ink": item["ink"],
                "stock": item,
                "cans_in_today": [],
                "cans_left_today": [],
            }
        else:
            comp["inks"][ink_id]["stock"] = item
        comp["total_cans_left"] += item["current_cans"]
        comp["total_weight_left"] += item["current_weight"]

    for txn in cans_in_today:
        comp = ensure_company(txn.ink_type.ink_company)
        ink_id = txn.ink_type.id
        if ink_id not in comp["inks"]:
            comp["inks"][ink_id] = {
                "ink": txn.ink_type,
                "stock": {
                    "initial_cans": float(txn.ink_type.initial_cans),
                    "initial_weight": float(txn.ink_type.initial_total_weight),
                    "cans_in": 0.0,
                    "used": 0.0,
                    "current_cans": float(txn.ink_type.initial_cans),
                    "current_weight": float(txn.ink_type.initial_total_weight),
                },
                "cans_in_today": [],
                "cans_left_today": [],
            }
        comp["inks"][ink_id]["cans_in_today"].append(txn)
        comp["cans_in_today"] += float(txn.quantity)

    for txn in cans_left_today:
        comp = ensure_company(txn.ink_type.ink_company)
        ink_id = txn.ink_type.id
        if ink_id not in comp["inks"]:
            comp["inks"][ink_id] = {
                "ink": txn.ink_type,
                "stock": {
                    "initial_cans": float(txn.ink_type.initial_cans),
                    "initial_weight": float(txn.ink_type.initial_total_weight),
                    "cans_in": 0.0,
                    "used": 0.0,
                    "current_cans": float(txn.ink_type.initial_cans),
                    "current_weight": float(txn.ink_type.initial_total_weight),
                },
                "cans_in_today": [],
                "cans_left_today": [],
            }
        comp["inks"][ink_id]["cans_left_today"].append(txn)
        comp["used_today"] += float(txn.quantity)

    result = []
    for company_name in sorted(companies.keys(), key=str.lower):
        comp = companies[company_name]
        inks = []
        for ink_id in sorted(
            comp["inks"].keys(),
            key=lambda iid: comp["inks"][iid]["ink"].name.lower(),
        ):
            inks.append(comp["inks"][ink_id])
        result.append(
            {
                "name": comp["name"],
                "inks": inks,
                "total_cans_left": comp["total_cans_left"],
                "total_weight_left": comp["total_weight_left"],
                "cans_in_today": comp["cans_in_today"],
                "used_today": comp["used_today"],
            }
        )
    return result
