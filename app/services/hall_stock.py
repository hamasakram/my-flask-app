"""In Hall and Machine Hall stock — separate from printing materials stock in/left."""

import json
import re
from datetime import datetime
from itertools import combinations

from app import db
from app.models import HallStockItem, HallStockMovement

ROLL_TOLERANCE = 0.05  # KG


# --------------------------------------------------------------------------- roll weights


def load_rolls(raw) -> list[float]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return []
    rolls = []
    for value in values if isinstance(values, list) else []:
        try:
            weight = round(float(value), 3)
        except (TypeError, ValueError):
            continue
        if weight > 0:
            rolls.append(weight)
    return rolls


def dump_rolls(rolls) -> str | None:
    rolls = [round(float(w), 3) for w in (rolls or []) if float(w) > 0]
    return json.dumps(rolls) if rolls else None


def _num(value: float) -> str:
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def group_rolls(rolls) -> list[tuple[float, int]]:
    groups: list[list] = []
    for weight in rolls or []:
        for group in groups:
            if abs(group[0] - weight) < 0.0005:
                group[1] += 1
                break
        else:
            groups.append([weight, 1])
    return [(w, n) for w, n in groups]


def format_roll_spec(rolls) -> str:
    """[168.8]*5 + [30] -> '168.8x5 + 30x1' (the shorthand used on the floor)."""
    return " + ".join(f"{_num(w)}x{n}" for w, n in group_rolls(rolls))


_ROLL_TOKEN = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(?:kgs?)?\s*(?:[x×*]\s*(\d+)\s*(?:rolls?)?)?$", re.IGNORECASE
)


def parse_roll_spec(text: str) -> list[float]:
    """Parse '168.8x5 + 30x1', '79x1 + 76+86+84+84 + 12', '81x1, 77x5' or '54+53kg'
    into one weight per roll. A number without 'xN' is one roll."""
    text = (text or "").strip()
    if not text:
        return []
    rolls: list[float] = []
    for token in re.split(r"[+,;\n]", text):
        token = token.strip()
        if not token:
            continue
        match = _ROLL_TOKEN.match(token)
        if not match:
            raise ValueError(
                f"Roll weights: can't read \"{token}\". Use weight x number of rolls, "
                "e.g. 168.8x5 + 30x1."
            )
        weight = float(match.group(1))
        count = int(match.group(2) or 1)
        if weight <= 0:
            raise ValueError("Roll weights: each roll must weigh more than 0 KG.")
        if count < 1 or count > 500:
            raise ValueError(f"Roll weights: \"{token}\" has an invalid number of rolls.")
        rolls.extend([round(weight, 3)] * count)
    return rolls


def _remove_rolls(available: list[float], picked: list[float]) -> list[float]:
    """Remove each picked weight once from available; raises if one isn't there."""
    remaining = list(available)
    for weight in picked:
        for index, candidate in enumerate(remaining):
            if abs(candidate - weight) < 0.0005:
                del remaining[index]
                break
        else:
            raise ValueError(f"A roll of {_num(weight)} kg is not in this material's roll list.")
    return remaining


def _auto_pick(available: list[float], kg: float, count: int | None) -> list[float] | None:
    """Find rolls whose weights add up to kg (and match the roll count when given)."""
    if not available or kg is None:
        return None
    sizes = [int(count)] if count else range(1, min(len(available), 4) + 1)
    unique_order = list(available)
    for size in sizes:
        if size < 1 or size > len(available) or size > 6:
            continue
        seen = set()
        for combo in combinations(range(len(unique_order)), size):
            weights = tuple(sorted(unique_order[i] for i in combo))
            if weights in seen:
                continue
            seen.add(weights)
            if abs(sum(weights) - kg) <= ROLL_TOLERANCE:
                return list(weights)
            if len(seen) > 20000:
                break
    return None


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
    # Round to grams so repeated adds/subtracts don't leave float noise (858.4000000000001).
    return round(float(current or 0) + float(incoming), 3)


def _subtract_optional(current, outgoing, label: str) -> float | None:
    if outgoing is None:
        return current
    available = float(current or 0)
    if outgoing > available + 0.0001:
        raise ValueError(f"{label} exceeds what is left ({available:,.3f}).")
    remaining = available - float(outgoing)
    if remaining <= 0.0001:
        return 0.0
    return round(remaining, 3)


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

    rolls_list = parse_roll_spec(form.get("roll_spec", ""))
    if rolls_list:
        # Roll weights given: they decide the KG total and the number of rolls.
        kg = round(sum(rolls_list), 3)
        rolls = float(len(rolls_list))

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
        "roll_list": rolls_list,
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
        if data.get("roll_list"):
            existing.roll_weights = dump_rolls(load_rolls(existing.roll_weights) + data["roll_list"])
        return existing, False

    item = HallStockItem(
        location=location,
        material_name=data["material_name"],
        material_type=data["material_type"],
        size=data["size"],
        micron=data["micron"],
        rolls_left=data["rolls_left"],
        kg=data["kg"],
        roll_weights=dump_rolls(data.get("roll_list")),
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
    new_rolls = data.get("roll_list") or []
    if new_rolls and sorted(new_rolls) == sorted(item.roll_list):
        # Roll list unchanged (e.g. only the micron was corrected): keep the stock KG/rolls
        # as they are, so rows with part-used rolls or unsplit KG are not changed silently.
        pass
    else:
        item.rolls_left = data["rolls_left"]
        item.kg = data["kg"]
    # Editing: the roll list on the form replaces the stored one (blank clears it).
    item.roll_weights = dump_rolls(data.get("roll_list"))


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
    item = db.session.get(HallStockItem, item_id)
    if not item or item.location != HallStockItem.LOCATION_IN_HALL:
        raise ValueError("Selected material is not in In Hall stock.")

    available_rolls = load_rolls(item.roll_weights)
    picked: list[float] = []
    for raw in form.getlist("roll_pick") if hasattr(form, "getlist") else []:
        try:
            weight = round(float(raw), 3)
        except (TypeError, ValueError):
            continue
        if weight > 0:
            picked.append(weight)
    if picked:
        remaining_rolls = _remove_rolls(available_rolls, picked)
        if gross_kg is None:
            gross_kg = round(sum(picked), 3)
        if rolls is None:
            rolls = float(len(picked))

    if gross_kg is None or gross_kg <= 0:
        raise ValueError("Gross weight (KG) to transfer must be greater than zero.")
    if rolls is not None and rolls < 0:
        raise ValueError("Rolls cannot be negative.")

    if not picked and available_rolls:
        auto = _auto_pick(available_rolls, gross_kg, int(rolls) if rolls and float(rolls).is_integer() else None)
        if auto:
            picked = auto
            remaining_rolls = _remove_rolls(available_rolls, picked)
        else:
            remaining_rolls = available_rolls

    item.kg = _subtract_optional(item.kg, gross_kg, "Gross weight")
    if rolls:
        item.rolls_left = _subtract_optional(item.rolls_left, rolls, "Rolls")
    if available_rolls:
        # Picked/matched rolls are removed. If the KG didn't match whole rolls the list is
        # kept as it is and the page flags the difference (HallStockItem.roll_excess_kg).
        item.roll_weights = dump_rolls(remaining_rolls)

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
        roll_weights=dump_rolls(picked),
        notes=notes or None,
        created_by_id=user_id,
    )
    db.session.add(movement)
    return movement


def undo_transfer(movement: HallStockMovement, user_id: int | None = None) -> dict:
    """Reverse a transfer to Machine Hall: take its KG/rolls off Machine Hall and put them back
    into In Hall, then remove the transfer record. Blocked if part of it was already used."""
    if movement.movement_type != HallStockMovement.TYPE_TRANSFER:
        raise ValueError("Only transfers to Machine Hall can be undone here.")

    kg = float(movement.gross_kg or 0)
    rolls = float(movement.rolls or 0)
    name = movement.material_name

    dest = db.session.get(HallStockItem, movement.dest_item_id) if movement.dest_item_id else None
    if dest is None or dest.location != HallStockItem.LOCATION_MACHINE:
        dest = find_matching_item(
            HallStockItem.LOCATION_MACHINE, name, movement.material_type or "",
            movement.size or "", movement.micron or "",
        )
    if dest is not None:
        left = float(dest.kg or 0)
        if kg > left + 0.0001:
            raise ValueError(
                f"Can't delete this transfer: {kg:,.3f} kg of {name} was transferred, but only "
                f"{left:,.3f} kg is left in Machine Hall (the rest was already used)."
            )
        dest.kg = _subtract_optional(dest.kg, kg, "Gross weight") if kg else dest.kg
        if rolls:
            dest.rolls_left = max(0.0, float(dest.rolls_left or 0) - rolls)
    elif kg > 0.0001:
        raise ValueError(
            f"Can't delete this transfer: {name} is no longer in Machine Hall stock."
        )

    source = db.session.get(HallStockItem, movement.source_item_id) if movement.source_item_id else None
    if source is None or source.location != HallStockItem.LOCATION_IN_HALL:
        source = find_matching_item(
            HallStockItem.LOCATION_IN_HALL, name, movement.material_type or "",
            movement.size or "", movement.micron or "",
        )
    moved_rolls = load_rolls(movement.roll_weights)
    if source is not None:
        source.kg = _add_optional(source.kg, kg)
        if rolls:
            source.rolls_left = _add_optional(source.rolls_left, rolls)
        if moved_rolls:
            source.roll_weights = dump_rolls(load_rolls(source.roll_weights) + moved_rolls)
    else:
        source = HallStockItem(
            location=HallStockItem.LOCATION_IN_HALL,
            material_name=name,
            material_type=movement.material_type or "",
            size=movement.size or "",
            micron=movement.micron or "",
            kg=kg,
            rolls_left=rolls or None,
            roll_weights=dump_rolls(moved_rolls),
            created_by_id=user_id,
        )
        db.session.add(source)

    db.session.delete(movement)
    db.session.flush()

    removed_machine_row = False
    if dest is not None and float(dest.kg or 0) <= 0.0001 and float(dest.rolls_left or 0) <= 0.0001:
        still_used = HallStockMovement.query.filter(
            db.or_(
                HallStockMovement.source_item_id == dest.id,
                HallStockMovement.dest_item_id == dest.id,
            )
        ).count()
        if not still_used:
            db.session.delete(dest)
            removed_machine_row = True
    db.session.flush()
    return {"name": name, "kg": kg, "rolls": rolls, "removed_machine_row": removed_machine_row}


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


def undo_machine_usage(movement: HallStockMovement, user_id: int | None = None) -> dict:
    """Reverse a Machine Hall usage record: put its KG/rolls back into Machine Hall stock,
    then remove the usage record."""
    if movement.movement_type != HallStockMovement.TYPE_USED:
        raise ValueError("Only Machine Hall usage records can be deleted here.")

    kg = float(movement.gross_kg or 0)
    rolls = float(movement.rolls or 0)
    name = movement.material_name

    item = db.session.get(HallStockItem, movement.source_item_id) if movement.source_item_id else None
    if item is None or item.location != HallStockItem.LOCATION_MACHINE:
        item = find_matching_item(
            HallStockItem.LOCATION_MACHINE, name, movement.material_type or "",
            movement.size or "", movement.micron or "",
        )
    created = False
    if item is not None:
        if kg:
            item.kg = _add_optional(item.kg, kg)
        if rolls:
            item.rolls_left = _add_optional(item.rolls_left, rolls)
    else:
        # The Machine Hall row was removed since — bring the material back as a new row.
        item = HallStockItem(
            location=HallStockItem.LOCATION_MACHINE,
            material_name=name,
            material_type=movement.material_type or "",
            size=movement.size or "",
            micron=movement.micron or "",
            kg=kg or None,
            rolls_left=rolls or None,
            created_by_id=user_id,
        )
        db.session.add(item)
        created = True

    db.session.delete(movement)
    db.session.flush()
    return {"name": name, "kg": kg, "rolls": rolls, "created_row": created, "item": item}
