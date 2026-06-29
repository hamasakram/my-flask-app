from datetime import date
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import AppSetting, Material, MaterialOpeningStock, MaterialTransaction


def create_material(
    company_id: int,
    name: str,
    size: str = "",
    category: str = "PET",
    micron: str = "",
) -> Material:
    """Always create a new material catalog entry (duplicates allowed)."""
    cleaned_name = name.strip()
    cleaned_size = (size or "").strip()
    cleaned_category = (category or "PET").strip().upper()
    cleaned_micron = (micron or "").strip()
    if not cleaned_name:
        raise ValueError("Material name is required.")

    material = Material(
        company_id=company_id,
        category=cleaned_category,
        name=cleaned_name,
        size=cleaned_size,
        micron=cleaned_micron or None,
    )
    db.session.add(material)
    db.session.flush()
    return material


def get_or_create_material(
    company_id: int,
    name: str,
    size: str = "",
    category: str = "PET",
    micron: str = "",
) -> Material:
    """Backward-compatible alias — always creates a new material."""
    return create_material(company_id, name, size, category, micron)


def get_low_stock_threshold(material: Material) -> int:
    if material.low_stock_threshold is not None:
        return material.low_stock_threshold
    setting = AppSetting.query.filter_by(key="default_material_low_stock_threshold").first()
    if setting:
        return int(setting.value)
    setting = AppSetting.query.filter_by(key="default_low_stock_threshold").first()
    if setting:
        return int(setting.value)
    return 50


def get_opening_quantity(material: Material) -> float:
    opening = MaterialOpeningStock.query.filter(
        func.lower(MaterialOpeningStock.material_name) == material.display_name.lower()
    ).first()
    return opening.quantity if opening else 0.0


def calculate_live_stock(
    company_id: Optional[int] = None,
    material_id: Optional[int] = None,
) -> list[dict]:
    query = Material.query
    if company_id:
        query = query.filter_by(company_id=company_id)
    if material_id:
        query = query.filter_by(id=material_id)

    materials = query.order_by(Material.company_id, Material.name, Material.size).all()
    results = []

    for material in materials:
        opening = get_opening_quantity(material)
        received = (
            db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
            .filter_by(
                company_id=material.company_id,
                material_id=material.id,
                transaction_type=MaterialTransaction.TRANSACTION_RECEIVED,
            )
            .scalar()
        )
        used = (
            db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
            .filter_by(
                company_id=material.company_id,
                material_id=material.id,
                transaction_type=MaterialTransaction.TRANSACTION_USED,
            )
            .scalar()
        )
        current = opening + float(received) - float(used)
        threshold = get_low_stock_threshold(material)

        results.append(
            {
                "company": material.company,
                "material": material,
                "opening": opening,
                "received": float(received),
                "used": float(used),
                "current": current,
                "threshold": threshold,
                "is_low": current <= threshold,
            }
        )

    return results


def get_current_stock(company_id: int, material_id: int) -> float:
    rows = calculate_live_stock(company_id=company_id, material_id=material_id)
    if not rows:
        return 0.0
    return rows[0]["current"]


def calculate_used_from_left(
    company_id: int, material_id: int, quantity_left: float
) -> float:
    current_stock = get_current_stock(company_id, material_id)
    if quantity_left > current_stock:
        raise ValueError(
            f"Quantity left ({quantity_left}) cannot exceed current stock ({current_stock:.1f} kg)."
        )
    return current_stock - quantity_left


def get_stock_usage_records(limit: int = 30) -> list[MaterialTransaction]:
    return (
        MaterialTransaction.query.filter_by(
            transaction_type=MaterialTransaction.TRANSACTION_USED
        )
        .order_by(
            MaterialTransaction.transaction_date.desc(),
            MaterialTransaction.id.desc(),
        )
        .limit(limit)
        .all()
    )


def get_dashboard_stats(today: date) -> dict:
    live_stock = calculate_live_stock()
    total_inventory = sum(item["current"] for item in live_stock)

    received_today = (
        db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
        .filter(
            MaterialTransaction.transaction_type
            == MaterialTransaction.TRANSACTION_RECEIVED,
            MaterialTransaction.transaction_date == today,
        )
        .scalar()
    )
    used_today = (
        db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
        .filter(
            MaterialTransaction.transaction_type == MaterialTransaction.TRANSACTION_USED,
            MaterialTransaction.transaction_date == today,
        )
        .scalar()
    )

    by_company: dict[str, float] = {}
    by_material: dict[str, float] = {}
    low_stock = []

    for item in live_stock:
        company_name = item["company"].name
        material_name = item["material"].display_name
        by_company[company_name] = by_company.get(company_name, 0) + item["current"]
        by_material[material_name] = by_material.get(material_name, 0) + item["current"]
        if item["is_low"]:
            low_stock.append(item)

    recent = (
        MaterialTransaction.query.order_by(
            MaterialTransaction.transaction_date.desc(),
            MaterialTransaction.id.desc(),
        )
        .limit(10)
        .all()
    )

    return {
        "total_inventory": total_inventory,
        "received_today": float(received_today),
        "used_today": float(used_today),
        "by_company": sorted(by_company.items(), key=lambda x: x[0]),
        "by_material": sorted(by_material.items(), key=lambda x: x[1], reverse=True)[:15],
        "low_stock": low_stock,
        "recent_transactions": recent,
    }
