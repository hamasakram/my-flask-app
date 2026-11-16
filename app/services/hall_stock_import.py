"""One-time replacement of In Hall stock with the count of 16 Nov 2026.

Runs once at start-up (flag in app_settings). Before removing the old In Hall rows it saves
a full copy of them in data_backups (key IN_HALL_BACKUP_KEY), and keeps transfer history:
transfers that pointed at a removed row simply lose that link (they still show and can
still be deleted — the stock then goes back as a new In Hall row).

Naming follows the existing hall rows so transfers merge into Machine Hall rows that are
already there: material name / type =
  PET / PET, Milky / OPP, Metalize / OPP, LD / Natural, LD / Milky, MPET / MPET,
  Metalize / CPP, Pearl / Pearl.
Sizes are in mm without the unit (as on existing rows). Each roll's weight is kept, so the
row's KG and roll count come from the rolls.
"""

from __future__ import annotations

import json

from app import db
from app.models import AppSetting, DataBackup, HallStockItem, HallStockMovement
from app.services.hall_stock import dump_rolls, parse_roll_spec

FLAG_KEY = "in_hall_replaced_2026_11_16"
IN_HALL_BACKUP_KEY = "in_hall_backup_2026_11_16"

# (material name, type, size mm, micron, rolls in "weight x count" shorthand)
IN_HALL_STOCK_2026_11_16 = [
    # PET
    ("PET", "PET", "835", "", "168.8x5 + 30x1"),
    ("PET", "PET", "780", "", "53x1"),
    ("PET", "PET", "810", "", "79x1 + 76 + 86 + 84 + 84 + 12"),
    ("PET", "PET", "427", "", "29x2"),
    ("PET", "PET", "660", "12", "132x1"),
    ("PET", "PET", "570", "", "2.8x1"),
    ("PET", "PET", "510", "", "12.5"),
    # OPP
    ("Milky", "OPP", "510", "", "87.5x3"),
    ("Metalize", "OPP", "810", "", "21 + 8.4 + 170x8"),
    ("Metalize", "OPP", "680", "", "20x1"),
    # LD Natural
    ("LD", "Natural", "610", "40", "54 + 53"),
    ("LD", "Natural", "660", "30", "55"),
    ("LD", "Natural", "570", "45", "50"),
    ("LD", "Natural", "660", "", "30"),
    ("LD", "Natural", "680", "30", "13.7"),
    ("LD", "Natural", "580", "40", "76x2"),
    ("LD", "Natural", "640", "40", "81x1 + 77x5"),
    ("LD", "Natural", "805", "60-65", "42.2x1"),
    ("LD", "Natural", "500", "75", "3.6x1"),
    ("LD", "Natural", "570", "80", "25.4x1"),
    ("LD", "Natural", "430", "50", "21.85x1"),
    ("LD", "Natural", "765", "130", "10.15x1 + 15.7x1"),
    ("LD", "Natural", "640", "35", "8.9x1"),
    ("LD", "Natural", "400", "40-45", "2.5x1"),
    ("LD", "Natural", "340", "", "13"),
    ("LD", "Natural", "780", "70", "24.4x1"),
    # LD Milky
    ("LD", "Milky", "480", "", "28 + 3.7"),
    ("LD", "Milky", "660", "", "34"),
    ("LD", "Milky", "765", "30", "35.6"),
    ("LD", "Milky", "765", "", "5.5"),
    ("LD", "Milky", "460", "70", "21"),
    ("LD", "Milky", "480", "70", "4.5"),
    # MPET
    ("MPET", "MPET", "610", "", "45 + 45.8"),
    ("MPET", "MPET", "380", "", "8"),
    ("MPET", "MPET", "765", "", "33x1"),
    ("MPET", "MPET", "680", "", "2.3x1"),
    ("MPET", "MPET", "570", "", "3x1"),
    # CPP Metalize
    ("Metalize", "CPP", "780", "", "63.6x1"),
    ("Metalize", "CPP", "810", "", "56x1 + 8.5x1"),
    ("Metalize", "CPP", "510", "", "18.7x1"),
    ("Metalize", "CPP", "660", "", "18.5x1"),
    ("Metalize", "CPP", "765", "", "4.9x1"),
    ("Metalize", "CPP", "640", "", "5.9x1"),
    ("Metalize", "CPP", "610", "", "12.6x1"),
    # Pearl
    ("Pearl", "Pearl", "680", "", "11.7"),
    ("Pearl", "Pearl", "660", "", "27.7"),
]


def new_in_hall_rows() -> list[dict]:
    rows = []
    for name, material_type, size, micron, spec in IN_HALL_STOCK_2026_11_16:
        rolls = parse_roll_spec(spec)
        rows.append(
            {
                "material_name": name,
                "material_type": material_type,
                "size": size,
                "micron": micron,
                "kg": round(sum(rolls), 3),
                "rolls_left": float(len(rolls)),
                "roll_weights": dump_rolls(rolls),
            }
        )
    return rows


def _row_snapshot(item: HallStockItem) -> dict:
    return {
        "id": item.id,
        "material_name": item.material_name,
        "material_type": item.material_type,
        "size": item.size,
        "micron": item.micron,
        "rolls_left": item.rolls_left,
        "kg": item.kg,
        "roll_weights": item.roll_weights,
        "created_by_id": item.created_by_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def replace_in_hall_stock_nov_2026() -> None:
    if AppSetting.query.filter_by(key=FLAG_KEY).first():
        return

    old_items = HallStockItem.query.filter_by(location=HallStockItem.LOCATION_IN_HALL).all()
    old_ids = [item.id for item in old_items]

    if not DataBackup.query.filter_by(key=IN_HALL_BACKUP_KEY).first():
        db.session.add(
            DataBackup(
                key=IN_HALL_BACKUP_KEY,
                payload=json.dumps([_row_snapshot(item) for item in old_items]),
            )
        )

    if old_ids:
        HallStockMovement.query.filter(HallStockMovement.source_item_id.in_(old_ids)).update(
            {HallStockMovement.source_item_id: None}, synchronize_session=False
        )
        HallStockMovement.query.filter(HallStockMovement.dest_item_id.in_(old_ids)).update(
            {HallStockMovement.dest_item_id: None}, synchronize_session=False
        )
        for item in old_items:
            db.session.delete(item)
        db.session.flush()

    for row in new_in_hall_rows():
        db.session.add(HallStockItem(location=HallStockItem.LOCATION_IN_HALL, **row))

    db.session.add(AppSetting(key=FLAG_KEY, value=f"replaced {len(old_ids)} rows"))
    db.session.commit()


# ---------------------------------------------------------------------------
# Machine Hall: count of 16 Nov 2026 (KG only; no roll counts were given)
# ---------------------------------------------------------------------------

MACHINE_FLAG_KEY = "machine_hall_replaced_2026_11_16"
MACHINE_BACKUP_KEY = "machine_hall_backup_2026_11_16"
# Transfers / usage records up to the stored movement id belong to the old counts: deleting
# them removes the history line only and does not move stock (see hall_stock.py).
STOCK_COUNT_KEY = "hall_stock_count_last_movement_id"

# (material name, type, size mm, micron, kg). Same size listed twice is added together
# into one row (PET 540 = 26 + 21.65, MPET 510 = 10.1 + 58.4).
MACHINE_HALL_STOCK_2026_11_16 = [
    # PET
    ("PET", "PET", "510", "", 8.35),
    ("PET", "PET", "810", "", 81),
    ("PET", "PET", "540", "", 26),
    ("PET", "PET", "570", "", 15.8),
    ("PET", "PET", "540", "", 21.65),
    ("PET", "PET", "765", "", 14),
    ("PET", "PET", "680", "", 65.8),
    ("PET", "PET", "855", "", 140),
    # OPP
    ("Heatsealable", "OPP", "765", "", 11.4),
    ("Metalize", "OPP", "810", "", 124),
    ("Metalize", "OPP", "680", "", 119.4),
    # LD Milky
    ("LD", "Milky", "640", "50", 39.7),
    ("LD", "Milky", "640", "40", 5.7),
    ("LD", "Milky", "640", "90", 15.55),
    ("LD", "Milky", "610", "50", 21.4),
    # LD Natural
    ("LD", "Natural", "570", "90", 58.9),
    ("LD", "Natural", "765", "40", 45.8),
    ("LD", "Natural", "740", "70", 31.1),
    ("LD", "Natural", "640", "40", 24),
    ("LD", "Natural", "485", "50", 5.95),
    ("LD", "Natural", "680", "50", 53.6),
    # MPET
    ("MPET", "MPET", "510", "", 10.1),
    ("MPET", "MPET", "640", "", 39.8),
    ("MPET", "MPET", "510", "", 58.4),
    # CPP Metalize
    ("Metalize", "CPP", "510", "", 33.5),
    ("Metalize", "CPP", "570", "", 19.1),
    ("Metalize", "CPP", "680", "", 21.8),
]


def new_machine_hall_rows() -> list[dict]:
    merged: dict[tuple, dict] = {}
    for name, material_type, size, micron, kg in MACHINE_HALL_STOCK_2026_11_16:
        key = (name.lower(), material_type.lower(), size, micron)
        if key in merged:
            merged[key]["kg"] = round(merged[key]["kg"] + kg, 3)
        else:
            merged[key] = {
                "material_name": name,
                "material_type": material_type,
                "size": size,
                "micron": micron,
                "kg": float(kg),
                "rolls_left": None,
                "roll_weights": None,
            }
    return list(merged.values())


def replace_machine_hall_stock_nov_2026() -> None:
    if AppSetting.query.filter_by(key=MACHINE_FLAG_KEY).first():
        return

    old_items = HallStockItem.query.filter_by(location=HallStockItem.LOCATION_MACHINE).all()
    old_ids = [item.id for item in old_items]

    if not DataBackup.query.filter_by(key=MACHINE_BACKUP_KEY).first():
        db.session.add(
            DataBackup(
                key=MACHINE_BACKUP_KEY,
                payload=json.dumps([_row_snapshot(item) for item in old_items]),
            )
        )

    if old_ids:
        HallStockMovement.query.filter(HallStockMovement.source_item_id.in_(old_ids)).update(
            {HallStockMovement.source_item_id: None}, synchronize_session=False
        )
        HallStockMovement.query.filter(HallStockMovement.dest_item_id.in_(old_ids)).update(
            {HallStockMovement.dest_item_id: None}, synchronize_session=False
        )
        for item in old_items:
            db.session.delete(item)
        db.session.flush()

    for row in new_machine_hall_rows():
        db.session.add(HallStockItem(location=HallStockItem.LOCATION_MACHINE, **row))

    # Every transfer/usage recorded so far (id <= this) belongs to the old counts.
    last_id = db.session.query(db.func.max(HallStockMovement.id)).scalar() or 0
    setting = AppSetting.query.filter_by(key=STOCK_COUNT_KEY).first()
    if setting:
        setting.value = str(last_id)
    else:
        db.session.add(AppSetting(key=STOCK_COUNT_KEY, value=str(last_id)))
    db.session.add(AppSetting(key=MACHINE_FLAG_KEY, value=f"replaced {len(old_ids)} rows"))
    db.session.commit()
