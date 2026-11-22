"""Material purchases received straight into In Hall or Machine Hall.

A purchase is its own record (date, supplier, bill no., material, KG / rolls / roll weights,
optional rate and amount). Saving it also adds the stock to the chosen hall using the same
matching rules as "Add Stock" (name + type + size + micron), so the hall pages, the PDFs and
Job Planning see it at once. Deleting a purchase takes that stock back out of the hall.
"""

from __future__ import annotations

from datetime import date, datetime

from app import db
from app.models import HallStockItem, HallStockMovement, HallStockPurchase
from app.services.hall_stock import (
    _add_optional,
    _subtract_optional,
    add_or_increase_stock,
    dump_rolls,
    find_matching_item,
    load_rolls,
    parse_stock_fields,
)

LOCATIONS = {
    HallStockItem.LOCATION_IN_HALL: "In Hall",
    HallStockItem.LOCATION_MACHINE: "Machine Hall",
}


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _parse_date(value: str, label: str = "Date") -> date:
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{label} is required.")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid date.") from exc


def parse_optional_date(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _optional_money(form, field: str, label: str) -> float | None:
    raw = (form.get(field) or "").replace(",", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")
    return value


def record_purchase(form, user_id: int | None) -> tuple[HallStockPurchase, HallStockItem, bool]:
    """Save a purchase and add its stock to the chosen hall. Returns (purchase, item, created_row)."""
    location = (form.get("location") or "").strip()
    if location not in LOCATIONS:
        raise ValueError("Choose where the material goes: In Hall or Machine Hall.")
    purchase_date = _parse_date(form.get("purchase_date"), "Purchase date")
    if not (form.get("kg") or "").strip() and not (form.get("roll_spec") or "").strip():
        raise ValueError("Enter the KG purchased (or the roll weights).")

    data = parse_stock_fields(form)
    kg = data["kg"]
    if kg is None or kg <= 0:
        raise ValueError("Enter the KG purchased (or the roll weights).")

    rate = _optional_money(form, "rate_per_kg", "Rate per KG")
    amount = _optional_money(form, "amount", "Amount")
    if rate is not None:
        amount = round(kg * rate, 2)

    item, created = add_or_increase_stock(location, data, user_id)
    db.session.flush()

    purchase = HallStockPurchase(
        location=location,
        purchase_date=purchase_date,
        supplier_name=_clean(form.get("supplier_name")),
        bill_no=_clean(form.get("bill_no")),
        item_id=item.id,
        # Use the hall row's own spelling (typing "pet" onto the "PET" row records "PET").
        material_name=item.material_name,
        material_type=item.material_type or "",
        size=item.size or "",
        micron=item.micron or "",
        kg=kg,
        rolls=data["rolls_left"],
        roll_weights=dump_rolls(data.get("roll_list")),
        rate_per_kg=rate,
        amount=amount,
        notes=(form.get("notes") or "").strip(),
        created_by_id=user_id,
    )
    db.session.add(purchase)
    db.session.flush()
    return purchase, item, created


def _remove_rolls_if_present(available: list[float], bought: list[float]) -> list[float]:
    remaining = list(available)
    for weight in bought:
        for index, candidate in enumerate(remaining):
            if abs(candidate - weight) < 0.0005:
                del remaining[index]
                break
    return remaining


def delete_purchase(purchase: HallStockPurchase) -> dict:
    """Take the purchase's stock back out of its hall and delete the record.

    Blocked when the hall no longer holds that much of the material (part of it was already
    transferred or used) - the stock would go wrong otherwise. If the stock row itself was
    deleted, only the record is removed.
    """
    kg = float(purchase.kg or 0)
    rolls = float(purchase.rolls or 0)
    name = purchase.material_name
    hall = LOCATIONS.get(purchase.location, "the hall")

    item = db.session.get(HallStockItem, purchase.item_id) if purchase.item_id else None
    if item is None or item.location != purchase.location:
        item = find_matching_item(
            purchase.location, name, purchase.material_type or "",
            purchase.size or "", purchase.micron or "",
        )

    removed_row = False
    if item is not None:
        left = float(item.kg or 0)
        if kg > left + 0.0001:
            raise ValueError(
                f"Can't delete this purchase: {kg:,.3f} kg of {name} was bought, but only "
                f"{left:,.3f} kg is left in {hall} (the rest was already moved or used). "
                "Correct the stock with Edit on the hall page instead."
            )
        item.kg = _subtract_optional(item.kg, kg, "KG") if kg else item.kg
        if rolls:
            item.rolls_left = max(0.0, float(item.rolls_left or 0) - rolls)
        bought_rolls = load_rolls(purchase.roll_weights)
        if bought_rolls and item.roll_weights:
            item.roll_weights = dump_rolls(_remove_rolls_if_present(load_rolls(item.roll_weights), bought_rolls))
    # No matching stock row any more (it was deleted by hand): the stock is already gone, so
    # only the purchase record is removed.
    history_only = item is None

    db.session.delete(purchase)
    db.session.flush()

    if item is not None and float(item.kg or 0) <= 0.0001 and float(item.rolls_left or 0) <= 0.0001:
        in_use = HallStockMovement.query.filter(
            db.or_(HallStockMovement.source_item_id == item.id, HallStockMovement.dest_item_id == item.id)
        ).count() + HallStockPurchase.query.filter_by(item_id=item.id).count()
        if not in_use:
            db.session.delete(item)
            removed_row = True
    db.session.flush()
    return {
        "name": name, "kg": kg, "rolls": rolls, "hall": hall,
        "removed_row": removed_row, "history_only": history_only,
    }


def detach_item(item_id: int) -> None:
    """Keep purchase records when their hall stock row is deleted (history stays)."""
    HallStockPurchase.query.filter_by(item_id=item_id).update(
        {HallStockPurchase.item_id: None}, synchronize_session=False
    )


def list_purchases(
    location: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str = "",
) -> list[HallStockPurchase]:
    query = HallStockPurchase.query
    if location in LOCATIONS:
        query = query.filter(HallStockPurchase.location == location)
    if date_from:
        query = query.filter(HallStockPurchase.purchase_date >= date_from)
    if date_to:
        query = query.filter(HallStockPurchase.purchase_date <= date_to)
    term = _clean(search).lower()
    if term:
        like = f"%{term}%"
        query = query.filter(
            db.or_(
                db.func.lower(HallStockPurchase.supplier_name).like(like),
                db.func.lower(HallStockPurchase.bill_no).like(like),
                db.func.lower(HallStockPurchase.material_name).like(like),
                db.func.lower(HallStockPurchase.material_type).like(like),
                db.func.lower(HallStockPurchase.size).like(like),
                db.func.lower(HallStockPurchase.notes).like(like),
            )
        )
    return query.order_by(
        HallStockPurchase.purchase_date.desc(), HallStockPurchase.id.desc()
    ).all()


def summarize(purchases: list[HallStockPurchase]) -> dict:
    """Totals for a list of purchases: overall, per hall and per supplier."""
    def blank():
        return {"count": 0, "kg": 0.0, "rolls": 0.0, "amount": 0.0, "priced": 0}

    total = blank()
    by_hall = {key: blank() for key in LOCATIONS}
    by_supplier: dict[str, dict] = {}
    supplier_label: dict[str, str] = {}
    for p in purchases:
        # Same supplier however it was capitalised ("Ali Packages" / "ali packages").
        key = (p.supplier_name or "").casefold() or "—"
        supplier_label.setdefault(key, p.supplier_name or "—")
        for bucket in (total, by_hall.setdefault(p.location, blank()),
                       by_supplier.setdefault(key, blank())):
            bucket["count"] += 1
            bucket["kg"] += float(p.kg or 0)
            bucket["rolls"] += float(p.rolls or 0)
            if p.amount is not None:
                bucket["amount"] += float(p.amount)
                bucket["priced"] += 1
    for bucket in [total, *by_hall.values(), *by_supplier.values()]:
        bucket["kg"] = round(bucket["kg"], 3)
        bucket["amount"] = round(bucket["amount"], 2)
    suppliers = sorted(
        ((supplier_label[key], bucket) for key, bucket in by_supplier.items()),
        key=lambda kv: (-kv[1]["kg"], kv[0].lower()),
    )
    return {"total": total, "by_hall": by_hall, "by_supplier": suppliers}


def supplier_names() -> list[str]:
    rows = (
        db.session.query(HallStockPurchase.supplier_name)
        .filter(HallStockPurchase.supplier_name != "")
        .distinct()
        .all()
    )
    names: dict[str, str] = {}
    for (name,) in rows:
        if name:
            names.setdefault(name.casefold(), name)
    return sorted(names.values(), key=str.lower)
