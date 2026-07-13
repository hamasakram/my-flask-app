from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import AppSetting, Material, MaterialOpeningStock, MaterialTransaction

USAGE_PERIODS = ("daily", "weekly", "monthly")


def create_material(
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
        category=cleaned_category,
        name=cleaned_name,
        size=cleaned_size,
        micron=cleaned_micron or None,
    )
    db.session.add(material)
    db.session.flush()
    return material


def get_or_create_material(
    name: str,
    size: str = "",
    category: str = "PET",
    micron: str = "",
) -> Material:
    """Backward-compatible alias — always creates a new material."""
    return create_material(name, size, category, micron)


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
    for record in MaterialOpeningStock.query.all():
        matched = find_material_for_opening_name(record.material_name)
        if matched and matched.id == material.id:
            return record.quantity
    return 0.0


def get_opening_stock_names() -> set[str]:
    names = {
        (name or "").strip().lower()
        for (name,) in db.session.query(MaterialOpeningStock.material_name).all()
        if name and name.strip()
    }
    return names


def material_matches_opening_stock(material: Material, opening_names: set[str] | None = None) -> bool:
    if opening_names is None:
        opening_names = get_opening_stock_names()
    name = material.name.strip().lower()
    display = material.display_name.strip().lower()
    if name in opening_names or display in opening_names:
        return True
    for record in MaterialOpeningStock.query.all():
        if find_material_for_opening_name(record.material_name) == material:
            return True
    return False


def parse_opening_material_name(text: str) -> dict:
    parts = [part.strip() for part in text.split("·") if part.strip()]
    if not parts:
        raise ValueError("Material name is required.")
    if len(parts) == 1:
        return {"category": "PET", "name": parts[0], "size": "", "micron": ""}

    category = parts[0].upper()
    name = parts[1]
    size = parts[2] if len(parts) > 2 else ""
    micron = parts[3].replace("μ", "").strip() if len(parts) > 3 else ""
    return {
        "category": category,
        "name": name,
        "size": size,
        "micron": micron,
    }


def find_material_for_opening_name(opening_name: str) -> Material | None:
    target = opening_name.strip().lower()
    for material in Material.query.all():
        if material.display_name.strip().lower() == target:
            return material
        if material.name.strip().lower() == target:
            return material
    return None


def find_or_create_material_for_opening_name(opening_name: str) -> Material:
    existing = find_material_for_opening_name(opening_name)
    if existing:
        return existing

    parsed = parse_opening_material_name(opening_name)
    return create_material(
        name=parsed["name"],
        size=parsed["size"],
        category=parsed["category"],
        micron=parsed["micron"],
    )


def get_material_options(*, context: str = "receive") -> list[dict]:
    """Build material dropdown options for purchase or usage forms."""
    materials = Material.query.order_by(Material.category, Material.name, Material.size).all()
    opening_names = get_opening_stock_names()
    options: list[dict] = []
    seen_material_ids: set[int] = set()

    def add_material_option(material: Material, *, in_opening_stock: bool):
        if material.id in seen_material_ids:
            return
        seen_material_ids.add(material.id)
        label = material.display_name
        if in_opening_stock:
            label = f"{label} (Opening Stock)"
        options.append(
            {
                "id": material.id,
                "name": label,
                "in_opening_stock": in_opening_stock,
            }
        )

    if context == "use":
        opening_records = MaterialOpeningStock.query.order_by(
            MaterialOpeningStock.material_name
        ).all()
        for record in opening_records:
            material = find_material_for_opening_name(record.material_name)
            if material:
                add_material_option(material, in_opening_stock=True)
            else:
                options.append(
                    {
                        "id": f"opening:{record.id}",
                        "name": f"{record.material_name} (Opening Stock)",
                        "in_opening_stock": True,
                    }
                )
        return options

    opening_records = MaterialOpeningStock.query.order_by(MaterialOpeningStock.material_name).all()
    for record in opening_records:
        material = find_material_for_opening_name(record.material_name)
        if material:
            add_material_option(material, in_opening_stock=True)
        else:
            options.append(
                {
                    "id": f"opening:{record.id}",
                    "name": f"{record.material_name} (Opening Stock)",
                    "in_opening_stock": True,
                }
            )

    for material in materials:
        add_material_option(
            material,
            in_opening_stock=material_matches_opening_stock(material, opening_names),
        )

    return options


def resolve_material_selection(material_ref: str) -> Material | None:
    if not material_ref:
        return None
    if material_ref.startswith("opening:"):
        opening_id = int(material_ref.split(":", 1)[1])
        opening = MaterialOpeningStock.query.get(opening_id)
        if not opening:
            return None
        return find_or_create_material_for_opening_name(opening.material_name)

    material_id = int(material_ref)
    return Material.query.filter_by(id=material_id).first()


def calculate_live_stock(
    material_id: Optional[int] = None,
) -> list[dict]:
    query = Material.query
    if material_id:
        query = query.filter_by(id=material_id)

    materials = query.order_by(Material.name, Material.size).all()
    results = []

    for material in materials:
        opening = get_opening_quantity(material)
        received = (
            db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
            .filter_by(
                material_id=material.id,
                transaction_type=MaterialTransaction.TRANSACTION_RECEIVED,
            )
            .scalar()
        )
        used = (
            db.session.query(func.coalesce(func.sum(MaterialTransaction.quantity), 0))
            .filter_by(
                material_id=material.id,
                transaction_type=MaterialTransaction.TRANSACTION_USED,
            )
            .scalar()
        )
        current = opening + float(received) - float(used)
        threshold = get_low_stock_threshold(material)

        results.append(
            {
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


def get_current_stock(material_id: int) -> float:
    rows = calculate_live_stock(material_id=material_id)
    if not rows:
        return 0.0
    return rows[0]["current"]


def calculate_used_from_left(material_id: int, quantity_left: float) -> float:
    current_stock = get_current_stock(material_id)
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


def get_usage_period_range(period: str, reference: date | None = None) -> tuple[date, date, str]:
    """Return start date, end date, and display label for a usage report period."""
    if period not in USAGE_PERIODS:
        raise ValueError(f"Invalid usage period: {period}")

    ref = reference or date.today()
    if period == "daily":
        return ref, ref, ref.strftime("%d %B %Y")

    if period == "weekly":
        start = ref - timedelta(days=ref.weekday())
        end = start + timedelta(days=6)
        if start.year == end.year:
            label = f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
        else:
            label = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"
        return start, end, label

    start = ref.replace(day=1)
    if ref.month == 12:
        end = ref.replace(day=31)
    else:
        end = ref.replace(month=ref.month + 1, day=1) - timedelta(days=1)
    return start, end, ref.strftime("%B %Y")


def _material_label(material: Material | None, material_id: int | None = None) -> str:
    if material:
        return material.display_name
    if material_id:
        return f"Unknown material #{material_id}"
    return "Unknown material"


def get_usage_report(period: str, reference: date | None = None) -> dict:
    """Build usage analytics for daily, weekly, or monthly stock-used records."""
    start_date, end_date, period_label = get_usage_period_range(period, reference)

    records = (
        MaterialTransaction.query.filter(
            MaterialTransaction.transaction_type == MaterialTransaction.TRANSACTION_USED,
            MaterialTransaction.transaction_date >= start_date,
            MaterialTransaction.transaction_date <= end_date,
        )
        .order_by(
            MaterialTransaction.transaction_date.desc(),
            MaterialTransaction.id.desc(),
        )
        .all()
    )

    by_material: dict[str, dict] = {}
    by_date: dict[str, dict] = {}
    total_used = 0.0

    for txn in records:
        total_used += txn.quantity
        material_name = _material_label(txn.material, txn.material_id)
        category = txn.material.category if txn.material else "—"

        material_row = by_material.setdefault(
            material_name,
            {
                "material_name": material_name,
                "category": category,
                "total_used": 0.0,
                "record_count": 0,
            },
        )
        material_row["total_used"] += txn.quantity
        material_row["record_count"] += 1

        date_key = txn.transaction_date.isoformat()
        date_row = by_date.setdefault(
            date_key,
            {"date": txn.transaction_date, "total_used": 0.0, "record_count": 0},
        )
        date_row["total_used"] += txn.quantity
        date_row["record_count"] += 1

    return {
        "period": period,
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
        "total_used": total_used,
        "record_count": len(records),
        "material_count": len(by_material),
        "records": records,
        "by_material": sorted(
            by_material.values(), key=lambda row: row["total_used"], reverse=True
        ),
        "by_date": sorted(by_date.values(), key=lambda row: row["date"]),
    }


def get_dashboard_stats(today: date, usage_period: str = "daily") -> dict:
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

    by_material: dict[str, float] = {}
    low_stock = []

    for item in live_stock:
        material_name = item["material"].display_name
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
        "by_material": sorted(by_material.items(), key=lambda x: x[1], reverse=True)[:15],
        "low_stock": low_stock,
        "recent_transactions": recent,
        "usage_report": get_usage_report(usage_period, today),
    }
