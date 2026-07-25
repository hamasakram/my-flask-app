from datetime import date
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import Material, MaterialTransaction
from app.services.companies import get_or_create_default_materials_company


def create_material(
    category: str,
    name: str,
    size: str = "",
    micron: str = "",
    initial_kg: float = 0,
) -> Material:
    cleaned_category = category.strip()
    cleaned_name = name.strip()
    cleaned_size = (size or "").strip()
    cleaned_micron = (micron or "").strip()

    if not cleaned_category or not cleaned_name:
        raise ValueError("Category name and material name are required.")

    company = get_or_create_default_materials_company()
    material = Material(
        company_id=company.id,
        category=cleaned_category,
        name=cleaned_name,
        size=cleaned_size,
        micron=cleaned_micron or None,
        initial_kg=float(initial_kg or 0),
    )
    db.session.add(material)
    db.session.flush()
    return material


def list_materials() -> list[Material]:
    return Material.query.order_by(Material.category, Material.name, Material.size).all()


def get_material_options() -> list[dict]:
    return [{"id": m.id, "name": m.display_name} for m in list_materials()]


def get_stock_in_total(material_id: int) -> float:
    return float(
        db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
        .filter_by(
            material_id=material_id,
            transaction_type=MaterialTransaction.TRANSACTION_STOCK_IN,
        )
        .scalar()
    )


def get_stock_used_total(material_id: int) -> float:
    return float(
        db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
        .filter_by(
            material_id=material_id,
            transaction_type=MaterialTransaction.TRANSACTION_STOCK_LEFT,
        )
        .scalar()
    )


def get_current_stock(material: Material) -> float:
    return float(material.initial_kg) + get_stock_in_total(material.id) - get_stock_used_total(
        material.id
    )


def calculate_live_stock(material_id: Optional[int] = None) -> list[dict]:
    query = Material.query
    if material_id:
        query = query.filter_by(id=material_id)

    results = []
    for material in query.order_by(Material.category, Material.name, Material.size).all():
        stock_in = get_stock_in_total(material.id)
        used = get_stock_used_total(material.id)
        current = float(material.initial_kg) + stock_in - used
        results.append(
            {
                "material": material,
                "initial_kg": float(material.initial_kg),
                "stock_in": stock_in,
                "used": used,
                "current": current,
            }
        )
    return results


def calculate_used_from_left(material_id: int, quantity_left: float) -> float:
    material = db.session.get(Material, material_id)
    if not material:
        raise ValueError("Material not found.")

    current_stock = get_current_stock(material)
    if quantity_left > current_stock:
        raise ValueError(
            f"Stock left ({quantity_left} kg) cannot exceed current stock ({current_stock:.1f} kg)."
        )
    return current_stock - quantity_left


def get_dashboard_stats(today: date) -> dict:
    live_stock = calculate_live_stock()
    total_inventory = sum(item["current"] for item in live_stock)

    stock_in_today = float(
        db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
        .filter(
            MaterialTransaction.transaction_type == MaterialTransaction.TRANSACTION_STOCK_IN,
            MaterialTransaction.transaction_date == today,
        )
        .scalar()
    )
    used_today = float(
        db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
        .filter(
            MaterialTransaction.transaction_type == MaterialTransaction.TRANSACTION_STOCK_LEFT,
            MaterialTransaction.transaction_date == today,
        )
        .scalar()
    )

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
        "stock_in_today": stock_in_today,
        "used_today": used_today,
        "live_stock": live_stock,
        "recent_transactions": recent,
    }


def get_daily_report_data(report_date: date) -> dict:
    live_stock = calculate_live_stock()
    day_txns = (
        MaterialTransaction.query.filter_by(transaction_date=report_date)
        .order_by(MaterialTransaction.id)
        .all()
    )

    stock_in_today = [
        t for t in day_txns if t.transaction_type == MaterialTransaction.TRANSACTION_STOCK_IN
    ]
    stock_left_today = [
        t for t in day_txns if t.transaction_type == MaterialTransaction.TRANSACTION_STOCK_LEFT
    ]

    return {
        "report_date": report_date,
        "live_stock": live_stock,
        "stock_in_today": stock_in_today,
        "stock_left_today": stock_left_today,
        "total_stock_in": sum(t.quantity for t in stock_in_today),
        "total_used": sum(t.quantity for t in stock_left_today),
    }
