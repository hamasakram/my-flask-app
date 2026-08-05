from datetime import datetime

from app import db
from app.models import ShClientLedgerEntry, ShClientLedgerLine, ShSupplierLedgerEntry, ShSupplierLedgerLine
from app.services.sh_bank import filter_by_bank, get_current_sh_bank_id
from app.services.sh_sale_invoice import (
    compute_current_balance,
    parse_invoice_lines,
)


def next_client_ledger_ref() -> str:
    year = datetime.now().year
    prefix = f"CL-{year}-"
    bank_id = get_current_sh_bank_id()
    query = ShClientLedgerEntry.query.filter(ShClientLedgerEntry.reference_number.like(f"{prefix}%"))
    if bank_id:
        query = query.filter(ShClientLedgerEntry.bank_id == bank_id)
    latest = query.order_by(ShClientLedgerEntry.id.desc()).first()
    if latest:
        try:
            seq = int(latest.reference_number.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = latest.id + 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def next_supplier_ledger_ref() -> str:
    year = datetime.now().year
    prefix = f"SL-{year}-"
    bank_id = get_current_sh_bank_id()
    query = ShSupplierLedgerEntry.query.filter(
        ShSupplierLedgerEntry.reference_number.like(f"{prefix}%")
    )
    if bank_id:
        query = query.filter(ShSupplierLedgerEntry.bank_id == bank_id)
    latest = query.order_by(ShSupplierLedgerEntry.id.desc()).first()
    if latest:
        try:
            seq = int(latest.reference_number.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = latest.id + 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def _save_ledger_lines(entry, lines: list[dict], line_model, fk_name: str) -> float:
    for line in list(entry.lines):
        db.session.delete(line)
    db.session.flush()

    total = 0.0
    for line_data in lines:
        db.session.add(
            line_model(
                **{fk_name: entry.id},
                line_number=line_data["line_number"],
                item_name=line_data["item_name"],
                size=line_data["size"],
                qty=line_data["qty"],
                qty_unit=line_data["qty_unit"],
                gross_weight=line_data["gross_weight"],
                net_weight=line_data["net_weight"],
                unit_price=line_data["unit_price"],
                line_total=line_data["line_total"],
            )
        )
        total += line_data["line_total"]
    return total


def save_client_ledger_lines(entry, lines: list[dict]) -> float:
    return _save_ledger_lines(entry, lines, ShClientLedgerLine, "entry_id")


def save_supplier_ledger_lines(entry, lines: list[dict]) -> float:
    return _save_ledger_lines(entry, lines, ShSupplierLedgerLine, "entry_id")


def get_client_ledger_entries() -> list[ShClientLedgerEntry]:
    query = ShClientLedgerEntry.query
    query = filter_by_bank(query, ShClientLedgerEntry)
    return query.order_by(
        ShClientLedgerEntry.entry_date.desc(), ShClientLedgerEntry.id.desc()
    ).all()


def get_supplier_ledger_entries() -> list[ShSupplierLedgerEntry]:
    query = ShSupplierLedgerEntry.query
    query = filter_by_bank(query, ShSupplierLedgerEntry)
    return query.order_by(
        ShSupplierLedgerEntry.entry_date.desc(), ShSupplierLedgerEntry.id.desc()
    ).all()


def build_client_ledger_from_form(form, user_id: int) -> ShClientLedgerEntry:
    from app.services.sh_bank import ensure_bank_on_create

    entry_date = form.get("entry_date") or form.get("invoice_date")
    reference = (form.get("reference_number") or form.get("invoice_number") or "").strip()
    factory_challan_no = form.get("factory_challan_no", "").strip()
    sold_to_id = form.get("sold_to_client_id", type=int)
    location = form.get("location", "MULTAN").strip() or "MULTAN"
    previous_balance = form.get("previous_balance", type=float) or 0.0
    previous_balance_type = form.get("previous_balance_type", "DR").strip() or "DR"
    current_balance_override = form.get("current_balance", type=float)
    current_balance_type = form.get("current_balance_type", "DR").strip() or "DR"
    notes = form.get("notes", "").strip()

    if not entry_date or not sold_to_id:
        raise ValueError("Date and client are required.")

    lines = parse_invoice_lines(form)
    parsed_date = datetime.strptime(entry_date, "%Y-%m-%d").date()

    entry = ShClientLedgerEntry(
        entry_date=parsed_date,
        reference_number=reference or next_client_ledger_ref(),
        factory_challan_no=factory_challan_no or None,
        sold_to_client_id=sold_to_id,
        location=location,
        previous_balance=previous_balance,
        previous_balance_type=previous_balance_type,
        current_balance_type=current_balance_type,
        notes=notes or None,
        created_by_id=user_id,
    )
    ensure_bank_on_create(entry)
    db.session.add(entry)
    db.session.flush()

    total_amount = save_client_ledger_lines(entry, lines)
    entry.total_amount = total_amount
    if current_balance_override is not None:
        entry.current_balance = current_balance_override
    else:
        current, balance_type = compute_current_balance(
            previous_balance, total_amount, previous_balance_type
        )
        entry.current_balance = current
        entry.current_balance_type = balance_type
    return entry


def build_supplier_ledger_from_form(form, user_id: int) -> ShSupplierLedgerEntry:
    from app.services.sh_bank import ensure_bank_on_create

    entry_date = form.get("entry_date") or form.get("invoice_date")
    reference = (form.get("reference_number") or form.get("invoice_number") or "").strip()
    factory_challan_no = form.get("factory_challan_no", "").strip()
    supplier_id = form.get("supplier_company_id", type=int)
    location = form.get("location", "MULTAN").strip() or "MULTAN"
    previous_balance = form.get("previous_balance", type=float) or 0.0
    previous_balance_type = form.get("previous_balance_type", "DR").strip() or "DR"
    current_balance_override = form.get("current_balance", type=float)
    current_balance_type = form.get("current_balance_type", "DR").strip() or "DR"
    notes = form.get("notes", "").strip()

    if not entry_date or not supplier_id:
        raise ValueError("Date and supplier are required.")

    lines = parse_invoice_lines(form)
    parsed_date = datetime.strptime(entry_date, "%Y-%m-%d").date()

    entry = ShSupplierLedgerEntry(
        entry_date=parsed_date,
        reference_number=reference or next_supplier_ledger_ref(),
        factory_challan_no=factory_challan_no or None,
        supplier_company_id=supplier_id,
        location=location,
        previous_balance=previous_balance,
        previous_balance_type=previous_balance_type,
        current_balance_type=current_balance_type,
        notes=notes or None,
        created_by_id=user_id,
    )
    ensure_bank_on_create(entry)
    db.session.add(entry)
    db.session.flush()

    total_amount = save_supplier_ledger_lines(entry, lines)
    entry.total_amount = total_amount
    if current_balance_override is not None:
        entry.current_balance = current_balance_override
    else:
        current, balance_type = compute_current_balance(
            previous_balance, total_amount, previous_balance_type
        )
        entry.current_balance = current
        entry.current_balance_type = balance_type
    return entry
