"""Sync bank payment ledger entries with client/supplier ledgers."""

from app import db
from app.models import ShClientLedgerEntry, ShLedgerEntry, ShSupplierLedgerEntry
from app.services.sh_bank import filter_by_bank, get_current_sh_bank_id, ensure_bank_on_create


ENTRY_SALE = "sale"
ENTRY_PAYMENT = "payment"


def get_last_client_ledger_balance(client_id: int) -> tuple[float, str]:
    query = ShClientLedgerEntry.query.filter(
        ShClientLedgerEntry.sold_to_client_id == client_id
    )
    query = filter_by_bank(query, ShClientLedgerEntry)
    last = query.order_by(
        ShClientLedgerEntry.entry_date.desc(), ShClientLedgerEntry.id.desc()
    ).first()
    if not last:
        return 0.0, "DR"
    return float(last.current_balance or 0), last.current_balance_type or "DR"


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


def sync_bank_entry_to_party_ledgers(bank_entry: ShLedgerEntry, user_id: int) -> list[str]:
    """Create client/supplier ledger payment rows from a bank ledger entry."""
    messages = []

    if bank_entry.client_company_id and float(bank_entry.credit or 0) > 0:
        existing = ShClientLedgerEntry.query.filter_by(
            source_bank_ledger_id=bank_entry.id
        ).first()
        if not existing:
            prev, prev_type = get_last_client_ledger_balance(bank_entry.client_company_id)
            amount = float(bank_entry.credit)
            current, current_type = balance_after_payment(prev, prev_type, amount)
            entry = ShClientLedgerEntry(
                entry_date=bank_entry.entry_date,
                reference_number=f"PAY-BL-{bank_entry.id}",
                sold_to_client_id=bank_entry.client_company_id,
                location="MULTAN",
                previous_balance=prev,
                previous_balance_type=prev_type,
                current_balance=current,
                current_balance_type=current_type,
                total_amount=amount,
                entry_type=ENTRY_PAYMENT,
                source_bank_ledger_id=bank_entry.id,
                notes=f"Payment received (bank ledger): {bank_entry.notes or ''}".strip(),
                created_by_id=user_id,
            )
            ensure_bank_on_create(entry)
            db.session.add(entry)
            messages.append("Client ledger updated with payment received.")

    if bank_entry.supplier_company_id and float(bank_entry.debit or 0) > 0:
        existing = ShSupplierLedgerEntry.query.filter_by(
            source_bank_ledger_id=bank_entry.id
        ).first()
        if not existing:
            prev, prev_type = get_last_supplier_ledger_balance(bank_entry.supplier_company_id)
            amount = float(bank_entry.debit)
            current, current_type = balance_after_payment(prev, prev_type, amount)
            entry = ShSupplierLedgerEntry(
                entry_date=bank_entry.entry_date,
                reference_number=f"PAY-BL-{bank_entry.id}",
                supplier_company_id=bank_entry.supplier_company_id,
                location="MULTAN",
                previous_balance=prev,
                previous_balance_type=prev_type,
                current_balance=current,
                current_balance_type=current_type,
                total_amount=amount,
                entry_type=ENTRY_PAYMENT,
                source_bank_ledger_id=bank_entry.id,
                notes=f"Payment sent (bank ledger): {bank_entry.notes or ''}".strip(),
                created_by_id=user_id,
            )
            ensure_bank_on_create(entry)
            db.session.add(entry)
            messages.append("Supplier ledger updated with payment sent.")

    return messages


def resync_bank_entry_party_ledgers(bank_entry: ShLedgerEntry, user_id: int) -> None:
    """Replace linked party ledger rows after a bank entry edit."""
    remove_linked_party_entries(bank_entry.id)
    sync_bank_entry_to_party_ledgers(bank_entry, user_id)


def remove_linked_party_entries(bank_ledger_id: int) -> None:
    ShClientLedgerEntry.query.filter_by(source_bank_ledger_id=bank_ledger_id).delete(
        synchronize_session=False
    )
    ShSupplierLedgerEntry.query.filter_by(source_bank_ledger_id=bank_ledger_id).delete(
        synchronize_session=False
    )
