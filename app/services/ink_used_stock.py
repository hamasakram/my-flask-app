from datetime import date
from typing import Optional

from sqlalchemy import func

from app import db
from app.models import InkType, UsedInkName, UsedInkShade, UsedInkStock


def list_used_ink_names() -> list[str]:
    return [row.name for row in UsedInkName.query.order_by(UsedInkName.name.asc()).all()]


def list_used_ink_shades() -> list[str]:
    return [row.name for row in UsedInkShade.query.order_by(UsedInkShade.name.asc()).all()]


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


def _find_catalog_ink(ink_name: str, shade_name: str) -> Optional[InkType]:
    query = InkType.query.filter(func.lower(InkType.name) == ink_name.lower())
    if shade_name:
        query = query.filter(func.lower(func.coalesce(InkType.color_code, "")) == shade_name.lower())
    return query.first()


def record_used_ink_stock(
    ink_name: str,
    shade_name: str,
    quantity: float,
    entry_date: date,
    source_transaction_id: Optional[int] = None,
    notes: str = "",
    created_by_id: Optional[int] = None,
    merge_same_day: bool = True,
) -> UsedInkStock:
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    resolved_ink = ensure_used_ink_name(ink_name)
    resolved_shade = ensure_used_ink_shade(shade_name) if shade_name else ""

    if source_transaction_id:
        linked = UsedInkStock.query.filter_by(source_transaction_id=source_transaction_id).first()
        if linked:
            linked.ink_name = resolved_ink
            linked.shade_name = resolved_shade
            linked.quantity_total = quantity
            linked.entry_date = entry_date
            if notes:
                linked.notes = notes
            return linked

    if merge_same_day:
        existing = UsedInkStock.query.filter_by(
            ink_name=resolved_ink,
            shade_name=resolved_shade,
            entry_date=entry_date,
        ).first()
        if existing and existing.source_transaction_id is None:
            existing.quantity_total += quantity
            if notes:
                existing.notes = notes
            return existing

    record = UsedInkStock(
        ink_name=resolved_ink,
        shade_name=resolved_shade,
        quantity_total=quantity,
        entry_date=entry_date,
        notes=notes,
        source_transaction_id=source_transaction_id,
        created_by_id=created_by_id,
    )
    db.session.add(record)
    db.session.flush()
    return record


def sync_used_ink_from_cans_out(
    ink: InkType,
    quantity_out: float,
    entry_date: date,
    transaction_id: int,
    created_by_id: Optional[int] = None,
) -> UsedInkStock:
    return record_used_ink_stock(
        ink_name=ink.name,
        shade_name=ink.color_code or "",
        quantity=quantity_out,
        entry_date=entry_date,
        source_transaction_id=transaction_id,
        created_by_id=created_by_id,
        merge_same_day=False,
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


def get_used_ink_stock_summary(entry_date: Optional[date] = None) -> list[dict]:
    entries = get_used_ink_stock_entries(entry_date)
    grouped: dict[tuple[str, str], dict] = {}

    for entry in entries:
        key = (entry.ink_name.lower(), entry.shade_name.lower())
        if key not in grouped:
            catalog_ink = _find_catalog_ink(entry.ink_name, entry.shade_name)
            grouped[key] = {
                "ink_name": entry.ink_name,
                "shade_name": entry.shade_name or "—",
                "quantity_total": 0.0,
                "weight_per_can": float(catalog_ink.weight_per_can) if catalog_ink else 0.0,
                "entries": [],
            }
        grouped[key]["quantity_total"] += float(entry.quantity_total)
        grouped[key]["entries"].append(entry)

    summary = []
    for item in grouped.values():
        item["total_weight"] = item["quantity_total"] * item["weight_per_can"]
        summary.append(item)

    return sorted(summary, key=lambda row: (row["ink_name"].lower(), row["shade_name"].lower()))


def get_used_ink_report_data(entry_date: Optional[date] = None) -> dict:
    summary = get_used_ink_stock_summary(entry_date)
    total_cans = sum(item["quantity_total"] for item in summary)
    total_weight = sum(item["total_weight"] for item in summary)
    return {
        "report_date": entry_date,
        "summary": summary,
        "total_cans": total_cans,
        "total_weight": total_weight,
        "entries": get_used_ink_stock_entries(entry_date),
    }
