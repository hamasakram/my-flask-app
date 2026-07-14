from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app import db
from app.models import AppSetting, Material, MaterialOpeningStock, MaterialTransaction

USAGE_PERIODS = ("daily", "weekly", "monthly")


def _normalize_name(value: str) -> str:
    return (value or "").strip().lower()


def _build_material_name_index(materials: list[Material]) -> dict[str, Material]:
    index: dict[str, Material] = {}
    for material in materials:
        for key in _material_lookup_keys(material):
            index.setdefault(key, material)
    return index


def _material_lookup_keys(material: Material) -> set[str]:
    keys = {
        _normalize_name(material.name),
        _normalize_name(material.display_name),
    }
    if material.size:
        keys.add(_normalize_name(f"{material.name} {material.size}"))
        keys.add(_normalize_name(f"{material.name}·{material.size}"))
    if material.category and material.size:
        keys.add(_normalize_name(f"{material.category} {material.name} {material.size}"))
        keys.add(
            _normalize_name(f"{material.category}·{material.name}·{material.size}")
        )
    return {key for key in keys if key}


def _opening_lookup_keys(opening_name: str) -> set[str]:
    keys = {_normalize_name(opening_name)}
    stripped = (opening_name or "").strip()
    if not stripped:
        return keys

    tokens = stripped.split()
    has_numeric_size = len(tokens) >= 2 and tokens[-1].replace(".", "", 1).isdigit()
    if has_numeric_size:
        name_part = " ".join(tokens[:-1])
        size_part = tokens[-1]
        keys.add(_normalize_name(f"{name_part} {size_part}"))
    elif len(tokens) == 1:
        keys.add(_normalize_name(tokens[0]))

    parts = [part.strip() for part in stripped.split("·") if part.strip()]
    if len(parts) >= 2:
        category = parts[0].upper()
        name = parts[1]
        size = parts[2] if len(parts) > 2 else ""
        if size:
            keys.add(_normalize_name(f"{name} {size}"))
            keys.add(_normalize_name(f"{category} {name} {size}"))
            keys.add(_normalize_name(f"{category}·{name}·{size}"))
        else:
            keys.add(_normalize_name(name))
    elif len(parts) == 1 and not has_numeric_size:
        keys.add(_normalize_name(parts[0]))

    return {key for key in keys if key}


def _build_opening_quantity_by_material_id(
    materials: list[Material] | None = None,
) -> dict[int, float]:
    if materials is None:
        materials = Material.query.all()
    name_index = _build_material_name_index(materials)
    quantities: dict[int, float] = {}
    for name, qty in db.session.query(
        MaterialOpeningStock.material_name, MaterialOpeningStock.quantity
    ).all():
        material = find_material_for_opening_name(name, name_index)
        if material:
            quantities[material.id] = quantities.get(material.id, 0.0) + float(qty)
    return quantities


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


def get_opening_quantity(
    material: Material,
    opening_lookup: dict[int, float] | None = None,
) -> float:
    if opening_lookup is None:
        opening_lookup = _build_opening_quantity_by_material_id([material])
    return opening_lookup.get(material.id, 0.0)


def get_opening_stock_names() -> set[str]:
    names: set[str] = set()
    for (name,) in db.session.query(MaterialOpeningStock.material_name).all():
        if name and name.strip():
            names.update(_opening_lookup_keys(name))
    return names


def get_opening_stock_display_names() -> dict[str, str]:
    """Map normalized opening-stock name to its display label."""
    names: dict[str, str] = {}
    for (name,) in (
        db.session.query(MaterialOpeningStock.material_name)
        .order_by(MaterialOpeningStock.material_name)
        .all()
    ):
        key = _normalize_name(name)
        if key and key not in names:
            names[key] = name.strip()
    return names


def _opening_quantity_by_name() -> dict[str, float]:
    quantities: dict[str, float] = {}
    for name, qty in db.session.query(
        MaterialOpeningStock.material_name, MaterialOpeningStock.quantity
    ).all():
        key = _normalize_name(name)
        if key:
            quantities[key] = quantities.get(key, 0.0) + float(qty)
    return quantities


class _OpeningStockOnlyMaterial:
    """Placeholder for opening-stock items not yet linked to the catalog."""

    def __init__(self, display_name: str):
        self.display_name = display_name
        self.name = display_name
        self.category = "—"
        self.id = None
        self.size = ""
        self.micron = None
        self.low_stock_threshold = None


def get_materials_in_opening_stock() -> list[Material]:
    opening_records = MaterialOpeningStock.query.order_by(
        MaterialOpeningStock.material_name
    ).all()
    if not opening_records:
        return []

    name_index = _build_material_name_index(
        Material.query.order_by(Material.category, Material.name, Material.size).all()
    )
    materials: list[Material] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for record in opening_records:
        norm = _normalize_name(record.material_name)
        if not norm or norm in seen_names:
            continue
        seen_names.add(norm)
        material = find_material_for_opening_name(record.material_name, name_index)
        if material and material.id not in seen_ids:
            seen_ids.add(material.id)
            materials.append(material)
    return materials


def material_matches_opening_stock(material: Material, opening_names: set[str] | None = None) -> bool:
    if opening_names is None:
        opening_names = get_opening_stock_names()
    return bool(_material_lookup_keys(material) & opening_names)


def parse_opening_material_name(text: str) -> dict:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("Material name is required.")

    parts = [part.strip() for part in stripped.split("·") if part.strip()]
    if len(parts) >= 2:
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

    tokens = stripped.split()
    if len(tokens) >= 2 and tokens[-1].replace(".", "", 1).isdigit():
        return {
            "category": "PET",
            "name": " ".join(tokens[:-1]),
            "size": tokens[-1],
            "micron": "",
        }

    return {"category": "PET", "name": stripped, "size": "", "micron": ""}


def find_material_for_opening_name(
    opening_name: str,
    name_index: dict[str, Material] | None = None,
) -> Material | None:
    normalized_opening = _normalize_name(opening_name)
    materials = Material.query.order_by(Material.category, Material.name, Material.size).all()
    if name_index is None:
        name_index = _build_material_name_index(materials)

    try:
        parsed = parse_opening_material_name(opening_name)
        if parsed["size"]:
            parsed_name = _normalize_name(parsed["name"])
            parsed_size = _normalize_name(parsed["size"])
            parsed_category = _normalize_name(parsed["category"])
            for material in materials:
                if _normalize_name(material.name) != parsed_name:
                    continue
                if _normalize_name(material.size or "") != parsed_size:
                    continue
                if _normalize_name(material.category) != parsed_category:
                    continue
                return material
            return None
    except ValueError:
        pass

    for material in materials:
        if _normalize_name(material.display_name) == normalized_opening:
            return material
        if _normalize_name(material.name) == normalized_opening and not (material.size or "").strip():
            return material

    for key in _opening_lookup_keys(opening_name):
        material = name_index.get(key)
        if material:
            return material
    return None


def get_opening_quantity_for_name(opening_name: str) -> float:
    """Sum opening-stock quantities for a material name label."""
    target = _normalize_name(opening_name)
    if not target:
        return 0.0
    total = 0.0
    for name, qty in db.session.query(
        MaterialOpeningStock.material_name, MaterialOpeningStock.quantity
    ).all():
        if _normalize_name(name) == target:
            total += float(qty)
    return total


def get_opening_quantity_for_material(material: Material) -> float:
    """Sum all opening-stock rows linked to a catalog material."""
    name_index = _build_material_name_index(Material.query.all())
    total = 0.0
    for name, qty in db.session.query(
        MaterialOpeningStock.material_name, MaterialOpeningStock.quantity
    ).all():
        matched = find_material_for_opening_name(name, name_index)
        if matched and matched.id == material.id:
            total += float(qty)
    return total


def get_opening_quantity_for_ref(material_ref: str) -> float:
    if material_ref.startswith("opening:"):
        opening_id = int(material_ref.split(":", 1)[1])
        record = MaterialOpeningStock.query.get(opening_id)
        if not record:
            return 0.0
        return get_opening_quantity_for_name(record.material_name)

    material = Material.query.filter_by(id=int(material_ref)).first()
    if not material:
        return 0.0
    return get_opening_quantity_for_material(material)


def get_current_stock_for_ref(material_ref: str) -> float:
    """Current stock for a dropdown/API selection: opening + purchased − used."""
    material = resolve_material_selection(material_ref)
    if not material:
        return 0.0

    opening = get_opening_quantity_for_ref(material_ref)
    txn_totals = _transaction_totals_by_material(material.id)
    received = txn_totals.get((material.id, MaterialTransaction.TRANSACTION_RECEIVED), 0.0)
    used = txn_totals.get((material.id, MaterialTransaction.TRANSACTION_USED), 0.0)
    return opening + received - used


def find_or_create_material_for_opening_name(opening_name: str) -> Material:
    materials = Material.query.all()
    name_index = _build_material_name_index(materials)
    existing = find_material_for_opening_name(opening_name, name_index)
    if existing:
        return existing

    parsed = parse_opening_material_name(opening_name)
    return create_material(
        name=parsed["name"],
        size=parsed["size"],
        category=parsed["category"],
        micron=parsed["micron"],
    )


def sync_opening_stock_material(material_name: str) -> Material:
    """Create or link a catalog material when opening stock is saved."""
    return find_or_create_material_for_opening_name(material_name)


def is_valid_opening_stock_selection(material_ref: str) -> bool:
    """Return True when a dropdown/API selection refers to opening stock."""
    if not material_ref:
        return False
    if material_ref.startswith("opening:"):
        try:
            opening_id = int(material_ref.split(":", 1)[1])
        except ValueError:
            return False
        return MaterialOpeningStock.query.get(opening_id) is not None
    try:
        material_id = int(material_ref)
    except ValueError:
        return False
    material = Material.query.filter_by(id=material_id).first()
    return material is not None and material_matches_opening_stock(material)


def _append_opening_stock_options(
    options: list[dict],
    opening_records: list[MaterialOpeningStock],
    name_index: dict[str, Material],
) -> None:
    seen_names: set[str] = set()
    for record in opening_records:
        norm = _normalize_name(record.material_name)
        if not norm or norm in seen_names:
            continue
        seen_names.add(norm)

        material = find_material_for_opening_name(record.material_name, name_index)
        options.append(
            {
                "id": f"opening:{record.id}",
                "name": record.material_name.strip(),
                "display_name": material.display_name if material else record.material_name.strip(),
                "in_opening_stock": True,
            }
        )


def get_material_options(*, context: str = "receive") -> list[dict]:
    """Build material dropdown options from opening stock records only."""
    _ = context
    opening_records = MaterialOpeningStock.query.order_by(
        MaterialOpeningStock.material_name
    ).all()
    if not opening_records:
        return []

    materials = Material.query.order_by(Material.category, Material.name, Material.size).all()
    name_index = _build_material_name_index(materials)
    options: list[dict] = []
    _append_opening_stock_options(options, opening_records, name_index)
    return sorted(options, key=lambda option: option["name"].lower())


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


def _transaction_totals_by_material(
    material_id: Optional[int] = None,
) -> dict[tuple[int, str], float]:
    query = db.session.query(
        MaterialTransaction.material_id,
        MaterialTransaction.transaction_type,
        func.coalesce(func.sum(MaterialTransaction.quantity), 0),
    )
    if material_id:
        query = query.filter(MaterialTransaction.material_id == material_id)
    query = query.group_by(
        MaterialTransaction.material_id,
        MaterialTransaction.transaction_type,
    )
    return {
        (mid, txn_type): float(qty)
        for mid, txn_type, qty in query.all()
    }


def calculate_live_stock(
    material_id: Optional[int] = None,
) -> list[dict]:
    opening_records = MaterialOpeningStock.query.order_by(
        MaterialOpeningStock.material_name
    ).all()
    if not opening_records:
        return []

    all_materials = Material.query.order_by(Material.name, Material.size).all()
    name_index = _build_material_name_index(all_materials)
    txn_totals = _transaction_totals_by_material(material_id)
    results = []
    seen_names: set[str] = set()

    for record in opening_records:
        norm = _normalize_name(record.material_name)
        if not norm or norm in seen_names:
            continue
        seen_names.add(norm)

        material = find_material_for_opening_name(record.material_name, name_index)
        if material_id is not None and (not material or material.id != material_id):
            continue

        opening = get_opening_quantity_for_name(record.material_name)
        if material:
            received = txn_totals.get(
                (material.id, MaterialTransaction.TRANSACTION_RECEIVED), 0.0
            )
            used = txn_totals.get(
                (material.id, MaterialTransaction.TRANSACTION_USED), 0.0
            )
            current = opening + received - used
            threshold = get_low_stock_threshold(material)
            row_material = material
        else:
            received = 0.0
            used = 0.0
            current = opening
            threshold = 50
            row_material = _OpeningStockOnlyMaterial(record.material_name.strip())

        results.append(
            {
                "material": row_material,
                "opening": opening,
                "received": received,
                "used": used,
                "current": current,
                "threshold": threshold,
                "is_low": current <= threshold,
            }
        )

    return results


def get_current_stock(material_id: int) -> float:
    material = Material.query.filter_by(id=material_id).first()
    if not material:
        return 0.0
    opening = get_opening_quantity_for_material(material)
    txn_totals = _transaction_totals_by_material(material_id)
    received = txn_totals.get((material_id, MaterialTransaction.TRANSACTION_RECEIVED), 0.0)
    used = txn_totals.get((material_id, MaterialTransaction.TRANSACTION_USED), 0.0)
    return opening + received - used


def calculate_used_from_left(material_id: int, quantity_left: float) -> float:
    current_stock = get_current_stock(material_id)
    if quantity_left > current_stock:
        raise ValueError(
            f"Quantity left ({quantity_left}) cannot exceed current stock ({current_stock:.1f} kg)."
        )
    return current_stock - quantity_left


def calculate_used_from_left_for_ref(material_ref: str, quantity_left: float) -> float:
    current_stock = get_current_stock_for_ref(material_ref)
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
        MaterialTransaction.query.options(
            joinedload(MaterialTransaction.material),
            joinedload(MaterialTransaction.created_by),
        )
        .filter(
            MaterialTransaction.transaction_type == MaterialTransaction.TRANSACTION_USED,
            MaterialTransaction.transaction_date >= start_date,
            MaterialTransaction.transaction_date <= end_date,
        )
        .order_by(
            MaterialTransaction.transaction_date.desc(),
            MaterialTransaction.id.desc(),
        )
        .limit(500)
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
        MaterialTransaction.query.options(joinedload(MaterialTransaction.material))
        .order_by(
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
