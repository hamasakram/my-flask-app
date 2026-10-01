"""In Hall and Machine Hall stock — separate from printing materials stock in/left."""

from datetime import datetime

from app import db
from app.models import HallStockItem, HallStockMovement


def _clean(value: str) -> str:
    return (value or "").strip()


def identity_key(name: str, material_type: str, size: str, micron: str) -> tuple:
    return (
        _clean(name).casefold(),
        _clean(material_type).casefold(),
        _clean(size).casefold(),
        _clean(micron).casefold(),
    )


def find_matching_item(
    location: str,
    name: str,
    material_type: str,
    size: str,
    micron: str,
    exclude_id: int | None = None,
) -> HallStockItem | None:
    key = identity_key(name, material_type, size, micron)
    query = HallStockItem.query.filter(HallStockItem.location == location)
    if exclude_id:
        query = query.filter(HallStockItem.id != exclude_id)
    for item in query.all():
        if identity_key(item.material_name, item.material_type, item.size, item.micron) == key:
            return item
    return None


def list_items(location: str) -> list[HallStockItem]:
    return (
        HallStockItem.query.filter(HallStockItem.location == location)
        .order_by(HallStockItem.material_name.asc(), HallStockItem.id.asc())
        .all()
    )


def list_movements(movement_type: str, limit: int = 80) -> list[HallStockMovement]:
    return (
        HallStockMovement.query.filter(HallStockMovement.movement_type == movement_type)
        .order_by(HallStockMovement.movement_date.desc(), HallStockMovement.id.desc())
        .limit(limit)
        .all()
    )


def _add_optional(current, incoming) -> float | None:
    if incoming is None:
        return current
    return float(current or 0) + float(incoming)


def _subtract_optional(current, outgoing, label: str) -> float | None:
    if outgoing is None:
        return current
    available = float(current or 0)
    if outgoing > available + 0.0001:
        raise ValueError(f"{label} exceeds what is left ({available:,.3f}).")
    remaining = available - float(outgoing)
    if remaining <= 0.0001:
        return 0.0
    return remaining


def parse_stock_fields(form, *, require_quantity: bool = True) -> dict:
    name = _clean(form.get("material_name", ""))
    material_type = _clean(form.get("material_type", ""))
    size = _clean(form.get("size", ""))
    micron = _clean(form.get("micron", ""))
    rolls = form.get("rolls_left", type=float)
    kg = form.get("kg", type=float)
    if form.get("rolls_left", "").strip() == "":
        rolls = None
    if form.get("kg", "").strip() == "":
        kg = None

    if not name:
        raise ValueError("Material name is required.")
    if not material_type:
        raise ValueError("Type of material is required.")
    if rolls is not None and rolls < 0:
        raise ValueError("Rolls cannot be negative.")
    if kg is not None and kg < 0:
        raise ValueError("KGs cannot be negative.")
    if require_quantity and (rolls is None or rolls == 0) and (kg is None or kg == 0):
        raise ValueError("Enter rolls, KGs, or both.")

    return {
        "material_name": name,
        "material_type": material_type,
        "size": size,
        "micron": micron,
        "rolls_left": rolls,
        "kg": kg,
    }


def add_or_increase_stock(location: str, data: dict, user_id: int | None) -> tuple[HallStockItem, bool]:
    existing = find_matching_item(
        location,
        data["material_name"],
        data["material_type"],
        data["size"],
        data["micron"],
    )
    if existing:
        existing.rolls_left = _add_optional(existing.rolls_left, data["rolls_left"])
        existing.kg = _add_optional(existing.kg, data["kg"])
        return existing, False

    item = HallStockItem(
        location=location,
        material_name=data["material_name"],
        material_type=data["material_type"],
        size=data["size"],
        micron=data["micron"],
        rolls_left=data["rolls_left"],
        kg=data["kg"],
        created_by_id=user_id,
    )
    db.session.add(item)
    return item, True


def update_item_fields(item: HallStockItem, data: dict) -> None:
    clash = find_matching_item(
        item.location,
        data["material_name"],
        data["material_type"],
        data["size"],
        data["micron"],
        exclude_id=item.id,
    )
    if clash:
        raise ValueError("Another row already has this material name, type, size, and micron.")
    item.material_name = data["material_name"]
    item.material_type = data["material_type"]
    item.size = data["size"]
    item.micron = data["micron"]
    item.rolls_left = data["rolls_left"]
    item.kg = data["kg"]


def transfer_to_machine_hall(form, user_id: int | None) -> HallStockMovement:
    item_id = form.get("item_id", type=int)
    gross_kg = form.get("gross_kg", type=float)
    rolls_raw = form.get("rolls", "")
    rolls = form.get("rolls", type=float) if str(rolls_raw).strip() else None
    notes = _clean(form.get("notes", ""))
    date_raw = form.get("movement_date")
    if not date_raw:
        raise ValueError("Date is required.")
    movement_date = datetime.strptime(date_raw, "%Y-%m-%d").date()

    if not item_id:
        raise ValueError("Select the In Hall material to transfer.")
    item = HallStockItem.query.get(item_id)
    if not item or item.location != HallStockItem.LOCATION_IN_HALL:
        raise ValueError("Selected material is not in In Hall stock.")
    if gross_kg is None or gross_kg <= 0:
        raise ValueError("Gross weight (KG) to transfer must be greater than zero.")
    if rolls is not None and rolls < 0:
        raise ValueError("Rolls cannot be negative.")

    item.kg = _subtract_optional(item.kg, gross_kg, "Gross weight")
    if rolls:
        item.rolls_left = _subtract_optional(item.rolls_left, rolls, "Rolls")

    dest = find_matching_item(
        HallStockItem.LOCATION_MACHINE,
        item.material_name,
        item.material_type,
        item.size or "",
        item.micron or "",
    )
    if dest:
        dest.kg = _add_optional(dest.kg, gross_kg)
        if rolls:
            dest.rolls_left = _add_optional(dest.rolls_left, rolls)
    else:
        dest = HallStockItem(
            location=HallStockItem.LOCATION_MACHINE,
            material_name=item.material_name,
            material_type=item.material_type,
            size=item.size or "",
            micron=item.micron or "",
            rolls_left=rolls,
            kg=gross_kg,
            created_by_id=user_id,
        )
        db.session.add(dest)
        db.session.flush()

    movement = HallStockMovement(
        movement_type=HallStockMovement.TYPE_TRANSFER,
        movement_date=movement_date,
        source_item_id=item.id,
        dest_item_id=dest.id,
        material_name=item.material_name,
        material_type=item.material_type,
        size=item.size or "",
        micron=item.micron or "",
        gross_kg=gross_kg,
        rolls=rolls,
        notes=notes or None,
        created_by_id=user_id,
    )
    db.session.add(movement)
    return movement


def record_machine_usage(form, user_id: int | None) -> HallStockMovement:
    item_id = form.get("item_id", type=int)
    used_kg = form.get("used_kg", type=float)
    rolls_raw = form.get("rolls", "")
    rolls = form.get("rolls", type=float) if str(rolls_raw).strip() else None
    where_used = _clean(form.get("where_used", ""))
    notes = _clean(form.get("notes", ""))
    date_raw = form.get("movement_date")
    if not date_raw:
        raise ValueError("Date is required.")
    movement_date = datetime.strptime(date_raw, "%Y-%m-%d").date()

    if not item_id:
        raise ValueError("Select which machine hall material was used.")
    item = HallStockItem.query.get(item_id)
    if not item or item.location != HallStockItem.LOCATION_MACHINE:
        raise ValueError("Selected material is not in Machine Hall stock.")
    if not where_used:
        raise ValueError("Write where this material was used.")
    if (used_kg is None or used_kg <= 0) and not rolls:
        raise ValueError("Enter how much was used (KG, rolls, or both).")
    if used_kg is not None and used_kg < 0:
        raise ValueError("Used KG cannot be negative.")
    if rolls is not None and rolls < 0:
        raise ValueError("Rolls cannot be negative.")

    if used_kg:
        item.kg = _subtract_optional(item.kg, used_kg, "Used KG")
    if rolls:
        item.rolls_left = _subtract_optional(item.rolls_left, rolls, "Rolls")

    movement = HallStockMovement(
        movement_type=HallStockMovement.TYPE_USED,
        movement_date=movement_date,
        source_item_id=item.id,
        material_name=item.material_name,
        material_type=item.material_type,
        size=item.size or "",
        micron=item.micron or "",
        gross_kg=used_kg,
        rolls=rolls,
        where_used=where_used,
        notes=notes or None,
        created_by_id=user_id,
    )
    db.session.add(movement)
    return movement
