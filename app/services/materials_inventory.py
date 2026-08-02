from datetime import date
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import Material, MaterialTransaction, ProductionJob
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
        "grouped_categories": group_report_by_category(live_stock, stock_in_today, stock_left_today),
        "job_production": group_job_production(stock_left_today),
    }


def group_report_by_category(live_stock, stock_in_today, stock_left_today) -> list[dict]:
    """Group stock and daily activity by category, then material."""
    categories: dict[str, dict] = {}

    def ensure_category(name: str) -> dict:
        key = name or "Uncategorized"
        if key not in categories:
            categories[key] = {
                "name": key,
                "materials": {},
                "total_left": 0.0,
                "stock_in_today": 0.0,
                "used_today": 0.0,
            }
        return categories[key]

    for item in live_stock:
        cat = ensure_category(item["material"].category)
        mat_id = item["material"].id
        if mat_id not in cat["materials"]:
            cat["materials"][mat_id] = {
                "material": item["material"],
                "stock": item,
                "stock_in_today": [],
                "stock_left_today": [],
            }
        else:
            cat["materials"][mat_id]["stock"] = item
        cat["total_left"] += item["current"]

    for txn in stock_in_today:
        cat = ensure_category(txn.material.category)
        mat_id = txn.material.id
        if mat_id not in cat["materials"]:
            cat["materials"][mat_id] = {
                "material": txn.material,
                "stock": {
                    "initial_kg": float(txn.material.initial_kg),
                    "stock_in": 0.0,
                    "used": 0.0,
                    "current": float(txn.material.initial_kg),
                },
                "stock_in_today": [],
                "stock_left_today": [],
            }
        cat["materials"][mat_id]["stock_in_today"].append(txn)
        cat["stock_in_today"] += float(txn.quantity)

    for txn in stock_left_today:
        cat = ensure_category(txn.material.category)
        mat_id = txn.material.id
        if mat_id not in cat["materials"]:
            cat["materials"][mat_id] = {
                "material": txn.material,
                "stock": {
                    "initial_kg": float(txn.material.initial_kg),
                    "stock_in": 0.0,
                    "used": 0.0,
                    "current": float(txn.material.initial_kg),
                },
                "stock_in_today": [],
                "stock_left_today": [],
            }
        cat["materials"][mat_id]["stock_left_today"].append(txn)
        cat["used_today"] += float(txn.quantity)

    result = []
    for cat_name in sorted(categories.keys(), key=str.lower):
        cat = categories[cat_name]
        materials = []
        for mat_id in sorted(
            cat["materials"].keys(),
            key=lambda mid: cat["materials"][mid]["material"].name.lower(),
        ):
            materials.append(cat["materials"][mat_id])
        result.append(
            {
                "name": cat["name"],
                "materials": materials,
                "total_left": cat["total_left"],
                "stock_in_today": cat["stock_in_today"],
                "used_today": cat["used_today"],
            }
        )
    return result


def list_production_job_names() -> list[str]:
    jobs = ProductionJob.query.order_by(ProductionJob.name.asc()).all()
    return [job.name for job in jobs]


def ensure_production_job(name: str) -> None:
    cleaned = name.strip()
    if not cleaned:
        return

    existing = ProductionJob.query.filter(
        func.lower(ProductionJob.name) == cleaned.lower()
    ).first()
    if existing:
        return

    db.session.add(ProductionJob(name=cleaned))


def parse_stock_left_usage_fields(form) -> dict:
    where_used = form.get("where_used", "").strip()
    used_in_printing = form.get("used_in_printing") == "on"
    used_in_lamination = form.get("used_in_lamination") == "on"
    total_production = form.get("total_production", type=float)

    printing_production = None
    lamination_production = None
    if total_production is not None and total_production >= 0:
        if used_in_printing:
            printing_production = total_production
        if used_in_lamination:
            lamination_production = total_production

    return {
        "where_used": where_used,
        "used_in_printing": used_in_printing,
        "used_in_lamination": used_in_lamination,
        "printing_production": printing_production,
        "lamination_production": lamination_production,
    }


def group_job_production(stock_left_today) -> list[dict]:
    """Group daily stock-left records by job for combined Printing/Lamination reporting."""
    jobs: dict[str, dict] = {}

    for txn in stock_left_today:
        job_name = (txn.where_used or "").strip() or "Unassigned"
        key = job_name.lower()
        if key not in jobs:
            jobs[key] = {
                "name": job_name,
                "printing_production": None,
                "lamination_production": None,
                "materials": [],
            }

        job = jobs[key]
        if txn.printing_production is not None:
            current = job["printing_production"]
            job["printing_production"] = (
                txn.printing_production if current is None else max(current, txn.printing_production)
            )
        if txn.lamination_production is not None:
            current = job["lamination_production"]
            job["lamination_production"] = (
                txn.lamination_production
                if current is None
                else max(current, txn.lamination_production)
            )

        usage_types = []
        if txn.used_in_printing:
            usage_types.append("Printing")
        if txn.used_in_lamination:
            usage_types.append("Lamination")

        job["materials"].append(
            {
                "material": txn.material.display_name,
                "quantity": float(txn.quantity),
                "types": ", ".join(usage_types) if usage_types else "—",
            }
        )

    return sorted(jobs.values(), key=lambda item: item["name"].lower())
