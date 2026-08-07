from datetime import datetime

from app import db
from app.models import (
    ShClientCompany,
    ShClientLedgerEntry,
    ShClientLedgerLine,
    ShSaleInvoice,
    ShSupplierCompany,
    ShSupplierLedgerEntry,
    ShSupplierLedgerLine,
)
from app.services.sh_bank import filter_by_bank, get_current_sh_bank_id
from app.services.sh_ledger_sync import (
    balance_after_sale,
    get_client_account_balance,
    get_client_ledger_balance_before,
    get_supplier_ledger_balance_before,
)
from app.services.sh_sale_invoice import parse_invoice_lines


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


def _summarize_client_party(client_id: int, entries: list) -> dict:
    bank_id = get_current_sh_bank_id()
    invoices = ShSaleInvoice.query.filter(ShSaleInvoice.sold_to_client_id == client_id)
    if bank_id:
        invoices = invoices.filter(
            db.or_(ShSaleInvoice.bank_id == bank_id, ShSaleInvoice.bank_id.is_(None))
        )
    invoice_sales = sum(float(inv.total_amount or 0) for inv in invoices.all())

    ledger_sales = 0.0
    total_payments = 0.0
    for entry in entries:
        amount = float(entry.total_amount or 0)
        if getattr(entry, "entry_type", "sale") == "payment":
            total_payments += amount
        else:
            ledger_sales += amount

    total_sales = invoice_sales if invoice_sales > 0 else ledger_sales
    remaining_balance, remaining_balance_type = get_client_account_balance(client_id)
    return {
        "total_sales": total_sales,
        "total_payments": total_payments,
        "remaining_balance": remaining_balance,
        "remaining_balance_type": remaining_balance_type,
    }


def _summarize_party_entries(entries: list) -> dict:
    total_sales = 0.0
    total_payments = 0.0
    for entry in entries:
        amount = float(entry.total_amount or 0)
        if getattr(entry, "entry_type", "sale") == "payment":
            total_payments += amount
        else:
            total_sales += amount
    last = entries[-1] if entries else None
    return {
        "total_sales": total_sales,
        "total_payments": total_payments,
        "remaining_balance": float(last.current_balance or 0) if last else 0.0,
        "remaining_balance_type": last.current_balance_type if last else "DR",
    }


def group_client_ledger_by_party(party_id: int | None = None) -> list[dict]:
    parties = ShClientCompany.query.order_by(ShClientCompany.name).all()
    if party_id:
        parties = [p for p in parties if p.id == party_id]

    grouped = []
    for party in parties:
        query = ShClientLedgerEntry.query.filter_by(sold_to_client_id=party.id)
        query = filter_by_bank(query, ShClientLedgerEntry)
        entries = query.order_by(
            ShClientLedgerEntry.entry_date.asc(), ShClientLedgerEntry.id.asc()
        ).all()
        invoices = _client_sale_invoices_for_party(party.id)
        if not entries and not invoices:
            continue
        summary = _summarize_client_party(party.id, entries)
        grouped.append({"party": party, "entries": entries, **summary})
    return grouped


def _client_sale_invoices_for_party(client_id: int) -> list[ShSaleInvoice]:
    bank_id = get_current_sh_bank_id()
    query = ShSaleInvoice.query.filter(ShSaleInvoice.sold_to_client_id == client_id)
    if bank_id:
        query = query.filter(
            db.or_(ShSaleInvoice.bank_id == bank_id, ShSaleInvoice.bank_id.is_(None))
        )
    return query.order_by(
        ShSaleInvoice.invoice_date.asc(), ShSaleInvoice.id.asc()
    ).all()


def group_supplier_ledger_by_party(party_id: int | None = None) -> list[dict]:
    parties = ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    if party_id:
        parties = [p for p in parties if p.id == party_id]

    grouped = []
    for party in parties:
        query = ShSupplierLedgerEntry.query.filter_by(supplier_company_id=party.id)
        query = filter_by_bank(query, ShSupplierLedgerEntry)
        entries = query.order_by(
            ShSupplierLedgerEntry.entry_date.asc(), ShSupplierLedgerEntry.id.asc()
        ).all()
        if not entries:
            continue
        summary = _summarize_party_entries(entries)
        grouped.append({"party": party, "entries": entries, **summary})
    return grouped


def build_client_ledger_from_form(form, user_id: int) -> ShClientLedgerEntry:
    from app.services.sh_bank import ensure_bank_on_create

    entry_date = form.get("entry_date") or form.get("invoice_date")
    reference = (form.get("reference_number") or form.get("invoice_number") or "").strip()
    factory_challan_no = form.get("factory_challan_no", "").strip()
    sold_to_id = form.get("sold_to_client_id", type=int)
    location = form.get("location", "MULTAN").strip() or "MULTAN"
    current_balance_override = form.get("current_balance", type=float)
    current_balance_type = form.get("current_balance_type", "DR").strip() or "DR"
    notes = form.get("notes", "").strip()

    if not entry_date or not sold_to_id:
        raise ValueError("Date and client are required.")

    lines = parse_invoice_lines(form)
    parsed_date = datetime.strptime(entry_date, "%Y-%m-%d").date()

    previous_balance, previous_balance_type = get_client_ledger_balance_before(
        sold_to_id, parsed_date
    )

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
        entry_type="sale",
    )
    ensure_bank_on_create(entry)
    db.session.add(entry)
    db.session.flush()

    total_amount = save_client_ledger_lines(entry, lines)
    entry.total_amount = total_amount
    if current_balance_override is not None:
        entry.current_balance = current_balance_override
    else:
        current, balance_type = balance_after_sale(
            previous_balance, previous_balance_type, total_amount
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
    current_balance_override = form.get("current_balance", type=float)
    current_balance_type = form.get("current_balance_type", "DR").strip() or "DR"
    notes = form.get("notes", "").strip()

    if not entry_date or not supplier_id:
        raise ValueError("Date and supplier are required.")

    lines = parse_invoice_lines(form)
    parsed_date = datetime.strptime(entry_date, "%Y-%m-%d").date()

    previous_balance, previous_balance_type = get_supplier_ledger_balance_before(
        supplier_id, parsed_date
    )

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
        entry_type="sale",
    )
    ensure_bank_on_create(entry)
    db.session.add(entry)
    db.session.flush()

    total_amount = save_supplier_ledger_lines(entry, lines)
    entry.total_amount = total_amount
    if current_balance_override is not None:
        entry.current_balance = current_balance_override
    else:
        current, balance_type = balance_after_sale(
            previous_balance, previous_balance_type, total_amount
        )
        entry.current_balance = current
        entry.current_balance_type = balance_type
    return entry
