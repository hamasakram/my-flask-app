from datetime import date
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import InkType, UsedInkName, UsedInkShade, UsedInkStock


def list_used_ink_names() -> list[str]:
    return [row.name for row in UsedInkName.query.order_by(UsedInkName.name.asc()).all()]


def list_used_ink_shades() -> list[str]:
    return [row.name for row in UsedInkShade.query.order_by(UsedInkShade.name.asc()).all()]


def list_used_ink_name_records() -> list[UsedInkName]:
    return UsedInkName.query.order_by(UsedInkName.name.asc()).all()


def list_used_ink_shade_records() -> list[UsedInkShade]:
    return UsedInkShade.query.order_by(UsedInkShade.name.asc()).all()


def create_catalog_ink_name(raw_name: str) -> UsedInkName:
    cleaned = (raw_name or "").strip()
    if not cleaned:
        raise ValueError("Ink name is required.")
    existing = UsedInkName.query.filter(func.lower(UsedInkName.name) == cleaned.lower()).first()
    if existing:
        raise ValueError("This ink name already exists.")
    record = UsedInkName(name=cleaned)
    db.session.add(record)
    db.session.flush()
    return record


def create_catalog_shade_name(raw_name: str) -> UsedInkShade:
    cleaned = (raw_name or "").strip()
    if not cleaned:
        raise ValueError("Shade name is required.")
    existing = UsedInkShade.query.filter(func.lower(UsedInkShade.name) == cleaned.lower()).first()
    if existing:
        raise ValueError("This shade name already exists.")
    record = UsedInkShade(name=cleaned)
    db.session.add(record)
    db.session.flush()
    return record


def catalog_ink_name_in_use(name: str) -> bool:
    return (
        UsedInkStock.query.filter(func.lower(UsedInkStock.ink_name) == name.lower()).count() > 0
    )


def catalog_shade_name_in_use(name: str) -> bool:
    return (
        UsedInkStock.query.filter(func.lower(UsedInkStock.shade_name) == name.lower()).count() > 0
    )


def get_linked_used_ink_stock(transaction_id: int) -> Optional[UsedInkStock]:
    return UsedInkStock.query.filter_by(source_transaction_id=transaction_id).first()


def _resolve_catalog_name(model, raw_name: str) -> str:
    cleaned = (raw_name or "").strip()
    if not cleaned:
        return ""

    existing = model.query.filter(func.lower(model.name) == cleaned.lower()).first()
    if existing:
        return existing.name

    record = model(name=cleaned)
    db.session.add(record)
    db.session.flush()
    return cleaned


def ensure_used_ink_name(raw_name: str) -> str:
    return _resolve_catalog_name(UsedInkName, raw_name)


def ensure_used_ink_shade(raw_name: str) -> str:
    return _resolve_catalog_name(UsedInkShade, raw_name)


def _stock_key(ink_name: str, shade_name: str) -> tuple[str, str]:
    return (ink_name.lower(), (shade_name or "").lower())


def get_used_ink_balance(ink_name: str, shade_name: str = "") -> float:
    cleaned_ink = (ink_name or "").strip()
    cleaned_shade = (shade_name or "").strip()
    if not cleaned_ink:
        return 0.0

    added = float(
        db.session.query(func.coalesce(func.sum(UsedInkStock.quantity_total), 0))
        .filter(
            func.lower(UsedInkStock.ink_name) == cleaned_ink.lower(),
            func.lower(UsedInkStock.shade_name) == cleaned_shade.lower(),
            UsedInkStock.entry_type == UsedInkStock.ENTRY_ADD,
        )
        .scalar()
    )
    used = float(
        db.session.query(func.coalesce(func.sum(UsedInkStock.quantity_total), 0))
        .filter(
            func.lower(UsedInkStock.ink_name) == cleaned_ink.lower(),
            func.lower(UsedInkStock.shade_name) == cleaned_shade.lower(),
            UsedInkStock.entry_type == UsedInkStock.ENTRY_USE,
        )
        .scalar()
    )
    return added - used


def get_used_ink_balances() -> list[dict]:
    """Current used ink stock levels in kg across all entries."""
    entries = UsedInkStock.query.order_by(
        UsedInkStock.ink_name.asc(),
        UsedInkStock.shade_name.asc(),
    ).all()
    grouped: dict[tuple[str, str], dict] = {}

    for entry in entries:
        key = _stock_key(entry.ink_name, entry.shade_name)
        if key not in grouped:
            grouped[key] = {
                "ink_name": entry.ink_name,
                "shade_name": entry.shade_name or "—",
                "added_kg": 0.0,
                "used_kg": 0.0,
                "balance_kg": 0.0,
            }
        qty = float(entry.quantity_total)
        if entry.entry_type == UsedInkStock.ENTRY_USE:
            grouped[key]["used_kg"] += qty
            grouped[key]["balance_kg"] -= qty
        else:
            grouped[key]["added_kg"] += qty
            grouped[key]["balance_kg"] += qty

    return sorted(
        grouped.values(),
        key=lambda row: (row["ink_name"].lower(), row["shade_name"].lower()),
    )


def record_used_ink_stock(
    ink_name: str,
    shade_name: str,
    quantity_kg: float,
    entry_date: date,
    source_transaction_id: Optional[int] = None,
    notes: str = "",
    created_by_id: Optional[int] = None,
    merge_same_day: bool = True,
    entry_type: str = UsedInkStock.ENTRY_ADD,
) -> UsedInkStock:
    if quantity_kg <= 0:
        raise ValueError("Quantity must be greater than zero.")

    resolved_ink = ensure_used_ink_name(ink_name)
    resolved_shade = ensure_used_ink_shade(shade_name) if shade_name else ""

    if source_transaction_id:
        linked = UsedInkStock.query.filter_by(source_transaction_id=source_transaction_id).first()
        if linked:
            linked.ink_name = resolved_ink
            linked.shade_name = resolved_shade
            linked.quantity_total = quantity_kg
            linked.entry_date = entry_date
            linked.entry_type = entry_type
            if notes:
                linked.notes = notes
            return linked

    if merge_same_day and entry_type == UsedInkStock.ENTRY_ADD:
        existing = UsedInkStock.query.filter_by(
            ink_name=resolved_ink,
            shade_name=resolved_shade,
            entry_date=entry_date,
            entry_type=UsedInkStock.ENTRY_ADD,
        ).first()
        if existing and existing.source_transaction_id is None:
            existing.quantity_total += quantity_kg
            if notes:
                existing.notes = notes
            return existing

    record = UsedInkStock(
        ink_name=resolved_ink,
        shade_name=resolved_shade,
        quantity_total=quantity_kg,
        entry_type=entry_type,
        entry_date=entry_date,
        notes=notes,
        source_transaction_id=source_transaction_id,
        created_by_id=created_by_id,
    )
    db.session.add(record)
    db.session.flush()
    return record


def record_used_ink_usage(
    ink_name: str,
    shade_name: str,
    quantity_kg: float,
    entry_date: date,
    notes: str = "",
    created_by_id: Optional[int] = None,
) -> UsedInkStock:
    balance = get_used_ink_balance(ink_name, shade_name)
    if quantity_kg > balance + 0.001:
        raise ValueError(
            f"Not enough used ink stock. Available: {balance:.1f} kg, requested: {quantity_kg:.1f} kg."
        )
    return record_used_ink_stock(
        ink_name=ink_name,
        shade_name=shade_name,
        quantity_kg=quantity_kg,
        entry_date=entry_date,
        notes=notes,
        created_by_id=created_by_id,
        merge_same_day=False,
        entry_type=UsedInkStock.ENTRY_USE,
    )


def sync_used_ink_from_cans_out(
    ink: InkType,
    quantity_out_cans: float,
    entry_date: date,
    transaction_id: int,
    ink_name: Optional[str] = None,
    shade_name: Optional[str] = None,
    notes: str = "",
    created_by_id: Optional[int] = None,
) -> UsedInkStock:
    quantity_kg = quantity_out_cans * float(ink.weight_per_can)
    if quantity_kg <= 0:
        raise ValueError("Cannot add zero kg to used ink stock from cans out.")

    return record_used_ink_stock(
        ink_name=ink_name or ink.name,
        shade_name=shade_name if shade_name is not None else (ink.color_code or ""),
        quantity_kg=quantity_kg,
        entry_date=entry_date,
        source_transaction_id=transaction_id,
        notes=notes,
        created_by_id=created_by_id,
        merge_same_day=False,
        entry_type=UsedInkStock.ENTRY_ADD,
    )


def get_used_ink_stock_entries(entry_date: Optional[date] = None) -> list[UsedInkStock]:
    query = UsedInkStock.query
    if entry_date:
        query = query.filter_by(entry_date=entry_date)
    return query.order_by(
        UsedInkStock.entry_date.desc(),
        UsedInkStock.ink_name.asc(),
        UsedInkStock.shade_name.asc(),
        UsedInkStock.id.desc(),
    ).all()


def get_used_ink_report_data(entry_date: Optional[date] = None) -> dict:
    balances = get_used_ink_balances()
    total_balance_kg = sum(item["balance_kg"] for item in balances)
    total_added_kg = sum(item["added_kg"] for item in balances)
    total_used_kg = sum(item["used_kg"] for item in balances)
    entries = get_used_ink_stock_entries(entry_date)
    daily_added_kg = sum(
        float(e.quantity_total) for e in entries if e.entry_type == UsedInkStock.ENTRY_ADD
    )
    daily_used_kg = sum(
        float(e.quantity_total) for e in entries if e.entry_type == UsedInkStock.ENTRY_USE
    )
    return {
        "report_date": entry_date,
        "balances": balances,
        "summary": balances,
        "total_balance_kg": total_balance_kg,
        "total_added_kg": total_added_kg,
        "total_used_kg": total_used_kg,
        "daily_added_kg": daily_added_kg,
        "daily_used_kg": daily_used_kg,
        "total_cans": total_balance_kg,
        "total_weight": total_balance_kg,
        "entries": entries,
    }
