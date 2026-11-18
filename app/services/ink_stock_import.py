"""One-time replacement of the Ink stock with the count of 18 Nov 2026.

Runs once at start-up (flag in app_settings). Before removing anything it saves a full copy
of the old inks, opening stock and Cans In / Cans Out records in data_backups
(key INK_BACKUP_KEY). Used Inks Stock entries are kept; their link to a removed Cans Out
record is cleared. Ink purchase receipts lose their link to the removed Cans In record.

Each ink: company (RL / Horizon Inks), colour code = code letters + number ("ALAM 0140";
Horizon inks have the number only), colour name, cans. 1 can = 25 kg. On the sheet
"25x3+1" means 3 cans + 1 more can = 4 cans. Black ALAM 7068 was listed twice (1 can each)
and is one ink with 2 cans.
"""

from __future__ import annotations

import json

from app import db
from app.models import (
    AppSetting,
    DataBackup,
    InkType,
    InventoryTransaction,
    OpeningStock,
    PrintJobInk,
    StockPurchaseReceipt,
    UsedInkStock,
)

FLAG_KEY = "ink_stock_replaced_2026_11_18"
INK_BACKUP_KEY = "ink_stock_backup_2026_11_18"
KG_PER_CAN = 25.0

# (company, colour code, colour name, cans)
INK_STOCK_2026_11_18 = [
    # RL
    ("RL", "ALAM 0140", "R-Media", 4),
    ("RL", "GLX 3042", "Magenta", 2),
    ("RL", "GLX 0049", "White", 2),
    ("RL", "GLX 5027", "Cyan", 1),
    ("RL", "GLX 0021", "R-Media", 2),
    ("RL", "GLX 1033", "Yellow", 1),
    ("RL", "ANZ 5113", "Cyan", 2),
    ("RL", "ALAM 7068", "Black", 1),
    ("RL", "ANZ 0136", "R-Media Black", 4),
    ("RL", "ANZ 3196", "Honey Red", 4),
    ("RL", "ANZ 0098", "R-Media", 1),
    ("RL", "ANZ 3140", "Magenta", 1),
    ("RL", "ALAM 7068", "Black", 1),
    ("RL", "VG 7052", "Black", 2),
    ("RL", "ANZ 6080", "Parrot Green", 5),
    ("RL", "ANZ 3191", "Red", 2),
    ("RL", "STR 0017", "White", 3),
    ("RL", "STR 0018", "R-Media", 1),
    ("RL", "STR 5015", "Cyan", 1),
    ("RL", "SHR 3081", "Red", 3),
    ("RL", "ALAM 3094", "Gold", 1),
    ("RL", "ANZ 1083", "Yellow", 2),
    ("RL", "ANZ 6090", "Light Green", 1),
    ("RL", "SHR 7056", "Black", 1),
    ("RL", "ANZ 3180", "D. Red", 1),
    # Horizon Inks
    ("Horizon Inks", "3115", "Magenta", 4),
    ("Horizon Inks", "0510", "White", 1),
    ("Horizon Inks", "1150", "P. Yellow", 1),
    ("Horizon Inks", "5160", "Cyan", 2),
]


def new_ink_rows() -> list[dict]:
    """The sheet as ink rows, same company + code + colour added together."""
    merged: dict[tuple, dict] = {}
    for company, code, colour, cans in INK_STOCK_2026_11_18:
        key = (company.lower(), code.lower(), colour.lower())
        if key in merged:
            merged[key]["initial_cans"] += cans
        else:
            merged[key] = {
                "ink_company": company,
                "color_code": code,
                "name": colour,
                "initial_cans": float(cans),
                "weight_per_can": KG_PER_CAN,
                "unit_type": "Can",
            }
    return list(merged.values())


def _snapshot(obj) -> dict:
    data = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.key)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        data[column.key] = value
    return data


def replace_ink_stock_nov_2026() -> None:
    if AppSetting.query.filter_by(key=FLAG_KEY).first():
        return
    from app.services.companies import get_or_create_default_ink_company

    inks = InkType.query.all()
    opening = OpeningStock.query.all()
    transactions = InventoryTransaction.query.all()

    if not DataBackup.query.filter_by(key=INK_BACKUP_KEY).first():
        db.session.add(
            DataBackup(
                key=INK_BACKUP_KEY,
                payload=json.dumps(
                    {
                        "ink_types": [_snapshot(i) for i in inks],
                        "opening_stock": [_snapshot(o) for o in opening],
                        "inventory_transactions": [_snapshot(t) for t in transactions],
                    }
                ),
            )
        )

    txn_ids = [t.id for t in transactions]
    if txn_ids:
        UsedInkStock.query.filter(UsedInkStock.source_transaction_id.in_(txn_ids)).update(
            {UsedInkStock.source_transaction_id: None}, synchronize_session=False
        )
        StockPurchaseReceipt.query.filter(
            StockPurchaseReceipt.inventory_transaction_id.in_(txn_ids)
        ).update({StockPurchaseReceipt.inventory_transaction_id: None}, synchronize_session=False)
    # Old job ink lines (inks are no longer used on jobs) keep their text, lose the link.
    PrintJobInk.query.update({PrintJobInk.ink_type_id: None}, synchronize_session=False)

    InventoryTransaction.query.delete(synchronize_session=False)
    OpeningStock.query.delete(synchronize_session=False)
    InkType.query.delete(synchronize_session=False)
    db.session.flush()

    company = get_or_create_default_ink_company()
    rows = new_ink_rows()
    for row in rows:
        db.session.add(InkType(company_id=company.id, **row))

    db.session.add(
        AppSetting(
            key=FLAG_KEY,
            value=f"replaced {len(inks)} inks, {len(transactions)} cans records; added {len(rows)} inks",
        )
    )
    db.session.commit()
