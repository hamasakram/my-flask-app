"""Sync bank payment ledger entries with client/supplier ledgers."""

from app import db
from app.models import ShClientLedgerEntry, ShLedgerEntry, ShSaleInvoice, ShSupplierLedgerEntry
from app.services.sh_bank import filter_by_bank, get_current_sh_bank_id


ENTRY_SALE = "sale"
ENTRY_PAYMENT = "payment"


def _entry_kind(entry) -> str:
    kind = getattr(entry, "entry_type", None) or ENTRY_SALE
    return ENTRY_PAYMENT if kind == ENTRY_PAYMENT else ENTRY_SALE


def _apply_client_ledger_entry(
    running: float, running_type: str, entry: ShClientLedgerEntry
) -> tuple[float, str]:
    amount = float(entry.total_amount or 0)
    if _entry_kind(entry) == ENTRY_PAYMENT:
        return balance_after_payment(running, running_type, amount)
    return balance_after_sale(running, running_type, amount)


def _client_sale_invoices(client_id: int, bank_id: int | None) -> list[ShSaleInvoice]:
    query = ShSaleInvoice.query.filter(ShSaleInvoice.sold_to_client_id == client_id)
    if bank_id:
        query = query.filter(
            db.or_(ShSaleInvoice.bank_id == bank_id, ShSaleInvoice.bank_id.is_(None))
        )
    return query.order_by(
        ShSaleInvoice.invoice_date.asc(), ShSaleInvoice.id.asc()
    ).all()


def _client_ledger_entries(client_id: int) -> list[ShClientLedgerEntry]:
    query = ShClientLedgerEntry.query.filter(
        ShClientLedgerEntry.sold_to_client_id == client_id
    )
    query = filter_by_bank(query, ShClientLedgerEntry)
    return query.order_by(
        ShClientLedgerEntry.entry_date.asc(), ShClientLedgerEntry.id.asc()
    ).all()


def get_client_account_balance(
    client_id: int,
    before_date=None,
    before_ledger_id: int | None = None,
    exclude_invoice_id: int | None = None,
) -> tuple[float, str]:
    """Client balance = total billed (sale invoices) minus payments received.

    When a client has sale invoices, those are the bill totals. Client ledger
    sale rows are ignored in that case to avoid double-counting legacy entries.
    Client ledger payment rows (including bank sync) reduce the balance.
    """
    bank_id = get_current_sh_bank_id()
    invoice_sales = 0.0
    for invoice in _client_sale_invoices(client_id, bank_id):
        if exclude_invoice_id and invoice.id == exclude_invoice_id:
            continue
        if before_date and invoice.invoice_date > before_date:
            continue
        invoice_sales += float(invoice.total_amount or 0)

    ledger_sales = 0.0
    total_payments = 0.0
    for entry in _client_ledger_entries(client_id):
        if before_date and entry.entry_date > before_date:
            continue
        if (
            before_ledger_id is not None
            and entry.entry_date == before_date
            and entry.id >= before_ledger_id
        ):
            continue
        amount = float(entry.total_amount or 0)
        if _entry_kind(entry) == ENTRY_PAYMENT:
            total_payments += amount
        else:
            ledger_sales += amount

    total_sales = invoice_sales if invoice_sales > 0 else ledger_sales
    balance = max(0.0, total_sales - total_payments)
    return balance, "DR"


def get_client_ledger_balance_before(
    client_id: int,
    before_date,
    before_id: int | None = None,
    exclude_invoice_id: int | None = None,
) -> tuple[float, str]:
    """Balance before a new client ledger row or sale invoice on before_date."""
    return get_client_account_balance(
        client_id,
        before_date=before_date,
        before_ledger_id=before_id,
        exclude_invoice_id=exclude_invoice_id,
    )


def get_last_client_ledger_balance(client_id: int) -> tuple[float, str]:
    return get_client_account_balance(client_id)


def _apply_supplier_ledger_entry(
    running: float, running_type: str, entry: ShSupplierLedgerEntry
) -> tuple[float, str]:
    amount = float(entry.total_amount or 0)
    if _entry_kind(entry) == ENTRY_PAYMENT:
        return balance_after_payment(running, running_type, amount)
    return balance_after_sale(running, running_type, amount)


def get_supplier_ledger_balance_before(
    supplier_id: int,
    before_date,
    before_id: int | None = None,
) -> tuple[float, str]:
    """Balance after all supplier ledger entries before (before_date, before_id)."""
    query = ShSupplierLedgerEntry.query.filter(
        ShSupplierLedgerEntry.supplier_company_id == supplier_id
    )
    query = filter_by_bank(query, ShSupplierLedgerEntry)
    entries = query.order_by(
        ShSupplierLedgerEntry.entry_date.asc(), ShSupplierLedgerEntry.id.asc()
    ).all()

    running = 0.0
    running_type = "DR"
    for entry in entries:
        if entry.entry_date > before_date:
            break
        if (
            before_id is not None
            and entry.entry_date == before_date
            and entry.id >= before_id
        ):
            break
        running, running_type = _apply_supplier_ledger_entry(running, running_type, entry)
    return running, running_type


def get_last_supplier_ledger_balance(supplier_id: int) -> tuple[float, str]:
    query = ShSupplierLedgerEntry.query.filter(
        ShSupplierLedgerEntry.supplier_company_id == supplier_id
    )
    query = filter_by_bank(query, ShSupplierLedgerEntry)
    last = query.order_by(
        ShSupplierLedgerEntry.entry_date.desc(), ShSupplierLedgerEntry.id.desc()
    ).first()
    if not last:
        return 0.0, "DR"
    return float(last.current_balance or 0), last.current_balance_type or "DR"


def balance_after_sale(previous: float, prev_type: str, amount: float) -> tuple[float, str]:
    balance_type = prev_type or "DR"
    return float(previous or 0) + float(amount or 0), balance_type


def balance_after_payment(previous: float, prev_type: str, amount: float) -> tuple[float, str]:
    balance_type = prev_type or "DR"
    new_balance = max(0.0, float(previous or 0) - float(amount or 0))
    return new_balance, balance_type


def recalculate_client_ledger_chain(client_id: int, bank_id: int) -> None:
    entries = (
        ShClientLedgerEntry.query.filter_by(
            sold_to_client_id=client_id,
            bank_id=bank_id,
        )
        .order_by(ShClientLedgerEntry.entry_date.asc(), ShClientLedgerEntry.id.asc())
        .all()
    )
    running = 0.0
    running_type = "DR"
    for entry in entries:
        entry.previous_balance = running
        entry.previous_balance_type = running_type
        running, running_type = _apply_client_ledger_entry(running, running_type, entry)
        entry.current_balance = running
        entry.current_balance_type = running_type


def recalculate_supplier_ledger_chain(supplier_id: int, bank_id: int) -> None:
    entries = (
        ShSupplierLedgerEntry.query.filter_by(
            supplier_company_id=supplier_id,
            bank_id=bank_id,
        )
        .order_by(ShSupplierLedgerEntry.entry_date.asc(), ShSupplierLedgerEntry.id.asc())
        .all()
    )
    running = 0.0
    running_type = "DR"
    for entry in entries:
        entry.previous_balance = running
        entry.previous_balance_type = running_type
        amount = float(entry.total_amount or 0)
        if _entry_kind(entry) == ENTRY_PAYMENT:
            running, running_type = balance_after_payment(running, running_type, amount)
        else:
            running, running_type = balance_after_sale(running, running_type, amount)
        entry.current_balance = running
        entry.current_balance_type = running_type


def _linked_client_bank_ids() -> set[int]:
    rows = (
        db.session.query(ShClientLedgerEntry.source_bank_ledger_id)
        .filter(ShClientLedgerEntry.source_bank_ledger_id.isnot(None))
        .all()
    )
    return {row[0] for row in rows}


def _linked_supplier_bank_ids() -> set[int]:
    rows = (
        db.session.query(ShSupplierLedgerEntry.source_bank_ledger_id)
        .filter(ShSupplierLedgerEntry.source_bank_ledger_id.isnot(None))
        .all()
    )
    return {row[0] for row in rows}


def sync_bank_entry_to_party_ledgers(bank_entry: ShLedgerEntry, user_id: int) -> list[str]:
    """Create client/supplier ledger payment rows from a bank ledger entry."""
    messages = []
    client_pairs: set[tuple[int, int]] = set()
    supplier_pairs: set[tuple[int, int]] = set()

    if bank_entry.client_company_id and float(bank_entry.credit or 0) > 0:
        existing = ShClientLedgerEntry.query.filter_by(
            source_bank_ledger_id=bank_entry.id
        ).first()
        if not existing and bank_entry.bank_id:
            amount = float(bank_entry.credit)
            entry = ShClientLedgerEntry(
                bank_id=bank_entry.bank_id,
                entry_date=bank_entry.entry_date,
                reference_number=f"PAY-BL-{bank_entry.id}",
                sold_to_client_id=bank_entry.client_company_id,
                location="MULTAN",
                previous_balance=0,
                previous_balance_type="DR",
                current_balance=0,
                current_balance_type="DR",
                total_amount=amount,
                entry_type=ENTRY_PAYMENT,
                source_bank_ledger_id=bank_entry.id,
                notes=f"Payment received (bank ledger): {bank_entry.notes or ''}".strip(),
                created_by_id=user_id,
            )
            db.session.add(entry)
            client_pairs.add((bank_entry.client_company_id, bank_entry.bank_id))
            messages.append("Client ledger updated with payment received.")

    if bank_entry.supplier_company_id and float(bank_entry.debit or 0) > 0:
        existing = ShSupplierLedgerEntry.query.filter_by(
            source_bank_ledger_id=bank_entry.id
        ).first()
        if not existing and bank_entry.bank_id:
            amount = float(bank_entry.debit)
            entry = ShSupplierLedgerEntry(
                bank_id=bank_entry.bank_id,
                entry_date=bank_entry.entry_date,
                reference_number=f"PAY-BL-{bank_entry.id}",
                supplier_company_id=bank_entry.supplier_company_id,
                location="MULTAN",
                previous_balance=0,
                previous_balance_type="DR",
                current_balance=0,
                current_balance_type="DR",
                total_amount=amount,
                entry_type=ENTRY_PAYMENT,
                source_bank_ledger_id=bank_entry.id,
                notes=f"Payment sent (bank ledger): {bank_entry.notes or ''}".strip(),
                created_by_id=user_id,
            )
            db.session.add(entry)
            supplier_pairs.add((bank_entry.supplier_company_id, bank_entry.bank_id))
            messages.append("Supplier ledger updated with payment sent.")

    db.session.flush()
    for client_id, bank_id in client_pairs:
        recalculate_client_ledger_chain(client_id, bank_id)
    for supplier_id, bank_id in supplier_pairs:
        recalculate_supplier_ledger_chain(supplier_id, bank_id)

    return messages


def resync_bank_entry_party_ledgers(bank_entry: ShLedgerEntry, user_id: int) -> None:
    """Replace linked party ledger rows after a bank entry edit."""
    client_id = bank_entry.client_company_id
    supplier_id = bank_entry.supplier_company_id
    bank_id = bank_entry.bank_id
    remove_linked_party_entries(bank_entry.id)
    sync_bank_entry_to_party_ledgers(bank_entry, user_id)
    if client_id and bank_id:
        recalculate_client_ledger_chain(client_id, bank_id)
    if supplier_id and bank_id:
        recalculate_supplier_ledger_chain(supplier_id, bank_id)


def remove_linked_party_entries(bank_ledger_id: int) -> None:
    ShClientLedgerEntry.query.filter_by(source_bank_ledger_id=bank_ledger_id).delete(
        synchronize_session=False
    )
    ShSupplierLedgerEntry.query.filter_by(source_bank_ledger_id=bank_ledger_id).delete(
        synchronize_session=False
    )


def recalculate_all_ledger_chains() -> None:
    """Recalculate every client/supplier ledger chain for all banks."""
    for row in db.session.query(
        ShClientLedgerEntry.sold_to_client_id, ShClientLedgerEntry.bank_id
    ).distinct():
        recalculate_client_ledger_chain(row[0], row[1])
    for row in db.session.query(
        ShSupplierLedgerEntry.supplier_company_id, ShSupplierLedgerEntry.bank_id
    ).distinct():
        recalculate_supplier_ledger_chain(row[0], row[1])


def sync_unsynced_bank_payments() -> dict[str, int]:
    """Import bank ledger client/supplier payments missing from party ledgers."""
    from sqlalchemy import inspect as sa_inspect

    from app.models import AppSetting
    from app.services.sh_bank import get_default_bank

    stats = {"client_payments": 0, "supplier_payments": 0}
    if not sa_inspect(db.engine).has_table("sh_ledger_entries"):
        return stats

    default_bank = get_default_bank()
    linked_clients = _linked_client_bank_ids()
    linked_suppliers = _linked_supplier_bank_ids()

    client_entries = (
        ShLedgerEntry.query.filter(
            ShLedgerEntry.client_company_id.isnot(None),
            ShLedgerEntry.credit > 0,
        )
        .order_by(ShLedgerEntry.entry_date.asc(), ShLedgerEntry.id.asc())
        .all()
    )
    for bank_entry in client_entries:
        if bank_entry.id in linked_clients:
            continue
        if not bank_entry.bank_id and default_bank:
            bank_entry.bank_id = default_bank.id
        if not bank_entry.bank_id:
            continue
        amount = float(bank_entry.credit)
        entry = ShClientLedgerEntry(
            bank_id=bank_entry.bank_id,
            entry_date=bank_entry.entry_date,
            reference_number=f"PAY-BL-{bank_entry.id}",
            sold_to_client_id=bank_entry.client_company_id,
            location="MULTAN",
            previous_balance=0,
            previous_balance_type="DR",
            current_balance=0,
            current_balance_type="DR",
            total_amount=amount,
            entry_type=ENTRY_PAYMENT,
            source_bank_ledger_id=bank_entry.id,
            notes=f"Payment received (bank ledger): {bank_entry.notes or ''}".strip(),
            created_by_id=bank_entry.created_by_id,
        )
        db.session.add(entry)
        stats["client_payments"] += 1

    supplier_entries = (
        ShLedgerEntry.query.filter(
            ShLedgerEntry.supplier_company_id.isnot(None),
            ShLedgerEntry.debit > 0,
        )
        .order_by(ShLedgerEntry.entry_date.asc(), ShLedgerEntry.id.asc())
        .all()
    )
    for bank_entry in supplier_entries:
        if bank_entry.id in linked_suppliers:
            continue
        if not bank_entry.bank_id and default_bank:
            bank_entry.bank_id = default_bank.id
        if not bank_entry.bank_id:
            continue
        amount = float(bank_entry.debit)
        entry = ShSupplierLedgerEntry(
            bank_id=bank_entry.bank_id,
            entry_date=bank_entry.entry_date,
            reference_number=f"PAY-BL-{bank_entry.id}",
            supplier_company_id=bank_entry.supplier_company_id,
            location="MULTAN",
            previous_balance=0,
            previous_balance_type="DR",
            current_balance=0,
            current_balance_type="DR",
            total_amount=amount,
            entry_type=ENTRY_PAYMENT,
            source_bank_ledger_id=bank_entry.id,
            notes=f"Payment sent (bank ledger): {bank_entry.notes or ''}".strip(),
            created_by_id=bank_entry.created_by_id,
        )
        db.session.add(entry)
        stats["supplier_payments"] += 1

    db.session.flush()
    recalculate_all_ledger_chains()

    flag_key = "sh_ledger_sync_v1"
    setting = AppSetting.query.filter_by(key=flag_key).first()
    if setting:
        setting.value = "done"
    else:
        db.session.add(AppSetting(key=flag_key, value="done"))
    db.session.commit()
    return stats


def backfill_unsynced_bank_payments() -> None:
    """Startup hook — always sync any missing bank payments, then recalculate."""
    sync_unsynced_bank_payments()


def fix_mashaallah_packages_bill() -> None:
    """Correct MashaAllah Packages purchase total (4,170,530 → 4,194,599)."""
    from datetime import date

    from app.models import AppSetting, ShSupplierCompany, ShSupplierLedgerLine

    flag_key = "fix_mashaallah_packages_bill_v1"
    if AppSetting.query.filter_by(key=flag_key).first():
        return

    supplier = (
        ShSupplierCompany.query.filter(
            db.func.lower(ShSupplierCompany.name).like("%masha%packages%")
        ).first()
    )
    if not supplier:
        return

    purchase_entries = (
        ShSupplierLedgerEntry.query.filter_by(
            supplier_company_id=supplier.id,
        )
        .filter(ShSupplierLedgerEntry.entry_type != ENTRY_PAYMENT)
        .order_by(
            ShSupplierLedgerEntry.entry_date.asc(),
            ShSupplierLedgerEntry.id.asc(),
        )
        .all()
    )
    if not purchase_entries:
        return

    expected_total = 4_194_599.0
    current_total = sum(float(entry.total_amount or 0) for entry in purchase_entries)
    if abs(current_total - expected_total) < 1:
        db.session.add(AppSetting(key=flag_key, value="already_correct"))
        db.session.commit()
        return

    wrong_total = 4_170_530.0
    if abs(current_total - wrong_total) > 1:
        return

    delta = expected_total - current_total
    target_entry = None
    for entry in reversed(purchase_entries):
        note = (entry.notes or "").lower()
        if "bopp" in note or entry.entry_date == date(2026, 6, 22):
            target_entry = entry
            break
    if not target_entry:
        target_entry = max(purchase_entries, key=lambda e: float(e.total_amount or 0))

    target_entry.total_amount = float(target_entry.total_amount or 0) + delta
    if target_entry.lines:
        last_line = target_entry.lines[-1]
        last_line.line_total = float(last_line.line_total or 0) + delta
    else:
        db.session.add(
            ShSupplierLedgerLine(
                entry_id=target_entry.id,
                line_number=1,
                item_name="Bill adjustment (660 BOPP)",
                size="",
                qty=0,
                qty_unit="KG",
                gross_weight=0,
                net_weight=0,
                unit_price=0,
                line_total=delta,
            )
        )

    bank_ids = {
        row[0]
        for row in db.session.query(ShSupplierLedgerEntry.bank_id)
        .filter_by(supplier_company_id=supplier.id)
        .distinct()
    }
    for bank_id in bank_ids:
        if bank_id:
            recalculate_supplier_ledger_chain(supplier.id, bank_id)

    db.session.add(AppSetting(key=flag_key, value="done"))
    db.session.commit()
