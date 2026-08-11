from datetime import date, timedelta
from typing import Optional

from app import db
from app.models import ShProfitLossRecord
from app.services.sh_bank import filter_by_bank, get_current_sh_bank


def line_total(quantity: float, rate: float) -> float:
    if not quantity or not rate:
        return 0.0
    return float(quantity) * float(rate)


def compute_profit_loss(
    purchase_kg: float,
    purchase_rate_per_kg: float,
    sold_kg: float,
    sold_rate_per_kg: float,
) -> dict:
    purchase_total = line_total(purchase_kg, purchase_rate_per_kg)
    sold_total = line_total(sold_kg, sold_rate_per_kg)
    net = sold_total - purchase_total
    if net >= 0:
        return {
            "purchase_total": purchase_total,
            "sold_total": sold_total,
            "profit_amount": net,
            "loss_amount": 0.0,
        }
    return {
        "purchase_total": purchase_total,
        "sold_total": sold_total,
        "profit_amount": 0.0,
        "loss_amount": abs(net),
    }


def parse_broker(raw: str) -> str:
    broker = (raw or "").strip().lower()
    if broker not in (ShProfitLossRecord.BROKER_HAMAS, ShProfitLossRecord.BROKER_AKRAM):
        raise ValueError("Select who purchased the stock: Hamas Broker or Akram Broker (Askari).")
    return broker


def build_record_from_form(form) -> dict:
    material_name = form.get("material_name", "").strip()
    size = form.get("size", "").strip()
    record_date = form.get("record_date")
    broker = parse_broker(form.get("broker"))
    notes = form.get("notes", "").strip()

    purchase_kg = form.get("purchase_kg", type=float)
    purchase_rate = form.get("purchase_rate_per_kg", type=float)
    sold_kg = form.get("sold_kg", type=float)
    sold_rate = form.get("sold_rate_per_kg", type=float)

    if not record_date:
        raise ValueError("Date is required.")
    if not material_name:
        raise ValueError("Material name is required.")
    if not purchase_kg or purchase_kg <= 0:
        raise ValueError("Quantity purchased (KG) must be greater than zero.")
    if not purchase_rate or purchase_rate <= 0:
        raise ValueError("Purchase rate per KG must be greater than zero.")
    if not sold_kg or sold_kg <= 0:
        raise ValueError("Total sold (KG) must be greater than zero.")
    if not sold_rate or sold_rate <= 0:
        raise ValueError("Sold rate per KG must be greater than zero.")

    amounts = compute_profit_loss(purchase_kg, purchase_rate, sold_kg, sold_rate)
    return {
        "record_date": record_date,
        "material_name": material_name,
        "size": size,
        "purchase_kg": purchase_kg,
        "purchase_rate_per_kg": purchase_rate,
        "sold_kg": sold_kg,
        "sold_rate_per_kg": sold_rate,
        "broker": broker,
        "notes": notes or None,
        **amounts,
    }


def apply_record_fields(record: ShProfitLossRecord, data: dict, record_date) -> None:
    from datetime import datetime

    record.record_date = (
        record_date
        if hasattr(record_date, "year")
        else datetime.strptime(str(record_date), "%Y-%m-%d").date()
    )
    record.material_name = data["material_name"]
    record.size = data["size"]
    record.purchase_kg = data["purchase_kg"]
    record.purchase_rate_per_kg = data["purchase_rate_per_kg"]
    record.purchase_total = data["purchase_total"]
    record.sold_kg = data["sold_kg"]
    record.sold_rate_per_kg = data["sold_rate_per_kg"]
    record.sold_total = data["sold_total"]
    record.profit_amount = data["profit_amount"]
    record.loss_amount = data["loss_amount"]
    record.broker = data["broker"]
    record.notes = data["notes"]


def scoped_records_query():
    return filter_by_bank(ShProfitLossRecord.query, ShProfitLossRecord).order_by(
        ShProfitLossRecord.record_date.desc(), ShProfitLossRecord.id.desc()
    )


def get_broker_summaries(records: list[ShProfitLossRecord]) -> dict:
    summary = {
        ShProfitLossRecord.BROKER_HAMAS: {
            "label": ShProfitLossRecord.BROKER_LABELS[ShProfitLossRecord.BROKER_HAMAS],
            "profit": 0.0,
            "loss": 0.0,
            "count": 0,
        },
        ShProfitLossRecord.BROKER_AKRAM: {
            "label": ShProfitLossRecord.BROKER_LABELS[ShProfitLossRecord.BROKER_AKRAM],
            "profit": 0.0,
            "loss": 0.0,
            "count": 0,
        },
    }
    for record in records:
        bucket = summary.get(record.broker)
        if not bucket:
            continue
        bucket["profit"] += float(record.profit_amount or 0)
        bucket["loss"] += float(record.loss_amount or 0)
        bucket["count"] += 1
    return summary


def get_dashboard_stats(today: date) -> dict:
    month_start = today.replace(day=1)
    query = scoped_records_query()
    month_records = query.filter(ShProfitLossRecord.record_date >= month_start).all()
    all_records = query.all()

    month_profit = sum(float(r.profit_amount or 0) for r in month_records)
    month_loss = sum(float(r.loss_amount or 0) for r in month_records)
    total_profit = sum(float(r.profit_amount or 0) for r in all_records)
    total_loss = sum(float(r.loss_amount or 0) for r in all_records)

    broker_summaries = get_broker_summaries(all_records)
    bank = get_current_sh_bank()

    return {
        "month_label": month_start.strftime("%B %Y"),
        "month_profit": month_profit,
        "month_loss": month_loss,
        "month_net": month_profit - month_loss,
        "month_count": len(month_records),
        "total_profit": total_profit,
        "total_loss": total_loss,
        "total_net": total_profit - total_loss,
        "record_count": len(all_records),
        "broker_summaries": broker_summaries,
        "recent_records": all_records[:10],
        "latest_record": all_records[0] if all_records else None,
        "current_bank": bank,
    }


def get_profit_loss_pdf_rows(broker: Optional[str] = None) -> list[dict]:
    query = scoped_records_query()
    if broker:
        query = query.filter(ShProfitLossRecord.broker == broker)
    rows = []
    for record in query.all():
        rows.append(
            {
                "date": record.record_date.strftime("%d-%m-%Y"),
                "material": record.material_name,
                "size": record.size or "—",
                "purchase_kg": f"{record.purchase_kg:,.1f}",
                "purchase_rate": f"{record.purchase_rate_per_kg:,.2f}",
                "purchase_total": f"{record.purchase_total:,.2f}",
                "sold_kg": f"{record.sold_kg:,.1f}",
                "sold_rate": f"{record.sold_rate_per_kg:,.2f}",
                "sold_total": f"{record.sold_total:,.2f}",
                "profit": f"{record.profit_amount:,.2f}" if record.profit_amount else "—",
                "loss": f"{record.loss_amount:,.2f}" if record.loss_amount else "—",
                "broker": record.broker_label,
                "notes": record.notes or "—",
            }
        )
    return rows
